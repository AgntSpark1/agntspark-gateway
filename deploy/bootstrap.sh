#!/usr/bin/env bash
# Provision (or update) a single-host AgntSpark production server.
#
#   sudo API_DOMAIN=agntapi.agntspark.com AGENT_DOMAIN=run.agntspark.com bash bootstrap.sh
#
# AGENT_DOMAIN needs a wildcard DNS record (*.run.agntspark.com) pointing at
# this host, DNS-only (not proxied) so on-demand certificates can be issued.
#
# Idempotent: safe to re-run to pull new code and restart the stack.
# Tested on Ubuntu 24.04 LTS (GCE image family ubuntu-2404-lts-amd64).
#
# Secrets live only in /opt/agntspark/.env (mode 600), generated on the first
# run and never regenerated — SECRET_ENCRYPTION_KEY in particular must be kept
# (and backed up) or every stored agent secret becomes unreadable.
set -euo pipefail

: "${API_DOMAIN:?Set API_DOMAIN, e.g. API_DOMAIN=agntapi.agntspark.com}"
: "${AGENT_DOMAIN:?Set AGENT_DOMAIN, e.g. AGENT_DOMAIN=run.agntspark.com}"
BASE=/opt/agntspark
ENV_FILE="$BASE/.env"
export DEBIAN_FRONTEND=noninteractive

echo "==> Packages"
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 git ufw openssl curl

echo "==> Code"
mkdir -p "$BASE"
for repo in agntspark-gateway agntspark-console agntspark-core agntspark-templates; do
  if [ -d "$BASE/$repo/.git" ]; then
    git -C "$BASE/$repo" fetch --depth 1 origin main
    git -C "$BASE/$repo" reset --hard origin/main
  else
    git clone --depth 1 "https://github.com/AgntSpark1/$repo" "$BASE/$repo"
  fi
done
DEPLOY="$BASE/agntspark-gateway/deploy"

echo "==> Docker daemon hardening"
install -m 0644 "$DEPLOY/daemon.json" /etc/docker/daemon.json
install -m 0644 "$DEPLOY/agntspark-block-metadata.service" /etc/systemd/system/agntspark-block-metadata.service
install -m 0644 "$DEPLOY/agntspark-isolate-agents.service" /etc/systemd/system/agntspark-isolate-agents.service
systemctl daemon-reload
systemctl enable docker
systemctl restart docker
systemctl enable --now agntspark-block-metadata.service agntspark-isolate-agents.service

echo "==> Host firewall (22/80/443 only)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Secrets"
if [ ! -f "$ENV_FILE" ]; then
  umask 077
  {
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
    echo "JWT_SECRET=$(openssl rand -hex 32)"
    # Fernet key: urlsafe base64 of 32 random bytes.
    echo "SECRET_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')"
  } > "$ENV_FILE"
  echo "    generated $ENV_FILE"
fi
# Secrets introduced after a host was first provisioned: added once, never rotated.
grep -q '^INGRESS_TOKEN=' "$ENV_FILE" || echo "INGRESS_TOKEN=$(openssl rand -hex 32)" >> "$ENV_FILE"
sed -i '/^API_DOMAIN=/d;/^AGENT_DOMAIN=/d;/^DOCKER_GID=/d' "$ENV_FILE"
echo "API_DOMAIN=$API_DOMAIN" >> "$ENV_FILE"
echo "AGENT_DOMAIN=$AGENT_DOMAIN" >> "$ENV_FILE"
echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "==> Console build"
# npm ci occasionally dies mid-install ("Exit handler never called") while
# still exiting 0, leaving node_modules half-populated — so verify the
# toolchain actually landed and retry instead of trusting the exit code.
docker run --rm -v "$BASE/agntspark-console:/app" -w /app node:20-alpine sh -c '
  for attempt in 1 2 3; do
    rm -rf node_modules
    npm ci --no-audit --no-fund && [ -x node_modules/.bin/tsc ] && [ -x node_modules/.bin/vite ] && break
    echo "npm ci attempt $attempt failed, retrying..." >&2
    [ "$attempt" = 3 ] && exit 1
    sleep 5
  done
  npm run build'

echo "==> Agent images"
# The default image every agent runs (runtime contract v1) and one image per
# official template built on top of it. Local to this host; nothing is pushed.
docker build -q -t agntspark/agent-runtime:latest "$BASE/agntspark-core"
for dir in "$BASE"/agntspark-templates/templates/*/; do
  name=$(basename "$dir")
  docker build -q --build-arg TEMPLATE="$name" -t "agntspark/template-$name:latest" \
    "$BASE/agntspark-templates"
done

echo "==> Stack"
cd "$DEPLOY"
# The agents network needs a fixed bridge name and subnet for the isolation
# rules; one created by an older deploy is replaced, and its agent containers
# are reattached once the stack is up.
RECONNECT_AGENTS=""
if docker network inspect agntspark-net >/dev/null 2>&1 &&
  [ "$(docker network inspect -f '{{index .Options "com.docker.network.bridge.name"}}' agntspark-net)" != "agntspark-agents" ]; then
  echo "    recreating agntspark-net with isolation settings"
  for c in $(docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' agntspark-net); do
    docker network disconnect -f agntspark-net "$c"
  done
  docker network rm agntspark-net
  RECONNECT_AGENTS=1
fi
docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml up -d --build --remove-orphans
if [ -n "$RECONNECT_AGENTS" ]; then
  for c in $(docker ps -aq --filter label=agntspark.agent_id); do
    docker network connect agntspark-net "$c"
  done
  # Agent IPs changed; the gateway re-reads them from Docker at startup.
  docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml restart gateway
fi

echo "==> Waiting for gateway health"
for _ in $(seq 1 60); do
  if docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml exec -T gateway \
      curl -fsS http://localhost:8080/healthz >/dev/null 2>&1; then
    echo "    gateway healthy"
    docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml ps
    exit 0
  fi
  sleep 5
done
echo "Gateway did not become healthy in time:" >&2
docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml logs --tail 80 gateway >&2
exit 1
