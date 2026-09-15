#!/usr/bin/env bash
# Nightly Postgres backup, run by agntspark-backup.timer.
#
# Writes a pg_dump custom-format archive to /opt/agntspark/backups (mode 600)
# and keeps 14 days. Restore with:
#
#   cd /opt/agntspark/agntspark-gateway/deploy
#   sudo docker compose --env-file /opt/agntspark/.env -f docker-compose.prod.yml \
#     exec -T postgres pg_restore --clean --if-exists -U agntspark -d agntspark_gateway \
#     < /opt/agntspark/backups/agntspark_gateway-<timestamp>.dump
#
# The local copies cover mistakes and bad migrations. To survive losing the
# VM, each archive is also copied to a Cloudflare R2 bucket once these are in
# /opt/agntspark/.env (an R2 API token scoped to that bucket, Object Read &
# Write):
#
#   R2_ACCOUNT_ID=...  R2_BUCKET=...  R2_ACCESS_KEY_ID=...  R2_SECRET_ACCESS_KEY=...
#
# Remote copies are kept R2_KEEP_DAYS (default 30). Fetch one with
# `rclone copy r2:<bucket>/<file> .` using the same credentials.
set -euo pipefail
umask 077

BASE=/opt/agntspark
DEST="$BASE/backups"
KEEP_DAYS=14
mkdir -p "$DEST"

env_value() {
  sed -n "s/^$1=//p" "$BASE/.env" | tail -1
}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$DEST/agntspark_gateway-$stamp.dump"

cd "$BASE/agntspark-gateway/deploy"
docker compose --env-file "$BASE/.env" -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U agntspark -d agntspark_gateway --format=custom > "$target.partial"
mv "$target.partial" "$target"

find "$DEST" -name 'agntspark_gateway-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name '*.partial' -mmin +60 -delete
echo "backup written: $target ($(stat -c %s "$target") bytes)"

r2_account=$(env_value R2_ACCOUNT_ID)
r2_bucket=$(env_value R2_BUCKET)
if [ -z "$r2_account" ] || [ -z "$r2_bucket" ]; then
  echo "off-host copy skipped: R2_ACCOUNT_ID / R2_BUCKET not set"
  exit 0
fi

# Credentials reach rclone through the environment (-e NAME), never its
# command line, so they don't show up in process listings.
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ENDPOINT="https://$r2_account.r2.cloudflarestorage.com"
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
RCLONE_CONFIG_R2_ACCESS_KEY_ID=$(env_value R2_ACCESS_KEY_ID)
RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$(env_value R2_SECRET_ACCESS_KEY)
export RCLONE_CONFIG_R2_ACCESS_KEY_ID RCLONE_CONFIG_R2_SECRET_ACCESS_KEY
remote_keep_days=$(env_value R2_KEEP_DAYS)

rclone() {
  docker run --rm -v "$DEST:/backups:ro" \
    -e RCLONE_CONFIG_R2_TYPE -e RCLONE_CONFIG_R2_PROVIDER -e RCLONE_CONFIG_R2_ENDPOINT \
    -e RCLONE_CONFIG_R2_NO_CHECK_BUCKET \
    -e RCLONE_CONFIG_R2_ACCESS_KEY_ID -e RCLONE_CONFIG_R2_SECRET_ACCESS_KEY \
    rclone/rclone:1 "$@"
}

rclone copy "/backups/$(basename "$target")" "r2:$r2_bucket/"
rclone delete --min-age "${remote_keep_days:-30}d" "r2:$r2_bucket/"
echo "off-host copy uploaded to r2:$r2_bucket"
