#!/usr/bin/env bash
# Update an already-provisioned host to the latest main of every repo.
#
#   sudo bash /opt/agntspark/agntspark-gateway/deploy/deploy.sh
#
# Brings the gateway checkout up to date first and then runs the new
# bootstrap.sh, so a deploy always runs the deploy logic it ships with. The
# domains come from the .env the first bootstrap wrote.
#
# Used by .github/workflows/deploy.yml through a forced command in the deploy
# user's authorized_keys, so the CI key can run this script and nothing else:
#
#   command="sudo /usr/bin/bash /opt/agntspark/agntspark-gateway/deploy/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-actions-deploy
set -euo pipefail

# Everything runs from main(), which bash has read in full before the git
# reset below rewrites this file.
main() {
  local base=/opt/agntspark
  local env_file="$base/.env"
  if [ ! -f "$env_file" ]; then
    echo "No $env_file: provision this host with bootstrap.sh first." >&2
    exit 1
  fi

  local api_domain agent_domain
  api_domain=$(sed -n 's/^API_DOMAIN=//p' "$env_file" | tail -1)
  agent_domain=$(sed -n 's/^AGENT_DOMAIN=//p' "$env_file" | tail -1)
  if [ -z "$api_domain" ] || [ -z "$agent_domain" ]; then
    echo "API_DOMAIN or AGENT_DOMAIN missing from $env_file." >&2
    exit 1
  fi

  # One deploy at a time: a push to main while one is running waits here.
  exec 9>/var/lock/agntspark-deploy.lock
  flock 9

  git -C "$base/agntspark-gateway" fetch -q --depth 1 origin main
  git -C "$base/agntspark-gateway" reset -q --hard origin/main
  echo "==> Deploying agntspark-gateway $(git -C "$base/agntspark-gateway" rev-parse --short HEAD)"

  API_DOMAIN="$api_domain" AGENT_DOMAIN="$agent_domain" \
    bash "$base/agntspark-gateway/deploy/bootstrap.sh"
}

main "$@"
