#!/usr/bin/env bash
# Provision (or update) a single-host AgntSpark production server.
#
#   sudo API_DOMAIN=api.agntspark.com bash bootstrap.sh
#
# Idempotent: safe to re-run to pull new code and restart the stack.
# Tested on Ubuntu 24.04 LTS (GCE image family ubuntu-2404-lts-amd64).
#
# Secrets live only in /opt/agntspark/.env (mode 600), generated on the first
# run and never regenerated — SECRET_ENCRYPTION_KEY in particular must be kept
# (and backed up) or every stored agent secret becomes unreadable.
set -euo pipefail

: "${API_DOMAIN:?Set API_DOMAIN, e.g. API_DOMAIN=api.agntspark.com}"
BASE=/opt/agntspark
ENV_FILE="$BASE/.env"
export DEBIAN_FRONTEND=noninteractive

echo "==> Packages"
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 git ufw openssl curl

echo "==> Code"
mkdir -p "$BASE"
for repo in agntspark-gateway agntspark-console; do
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
systemctl daemon-reload
systemctl enable docker
systemctl restart docker
systemctl enable --now agntspark-block-metadata.service

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
sed -i '/^API_DOMAIN=/d;/^DOCKER_GID=/d' "$ENV_FILE"
echo "API_DOMAIN=$API_DOMAIN" >> "$ENV_FILE"
echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "==> Console build"
docker run --rm -v "$BASE/agntspark-console:/app" -w /app node:20-alpine \
  sh -c "npm ci --no-audit --no-fund && npm run build"

echo "==> Stack"
cd "$DEPLOY"
docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml up -d --build --remove-orphans

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
