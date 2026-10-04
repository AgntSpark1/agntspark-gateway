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
#
# Every archive is test-restored into a scratch database before it counts:
# a backup nobody has restored is a hope, not a backup. With BACKUP_PING_URL
# in .env (a healthchecks.io-style check, e.g. https://hc-ping.com/<uuid>),
# success pings that URL and any failure pings <url>/fail, so a backup that
# fails or silently stops running raises an alert.
set -Eeuo pipefail
umask 077

BASE=/opt/agntspark
DEST="$BASE/backups"
KEEP_DAYS=14
mkdir -p "$DEST"

env_value() {
  sed -n "s/^$1=//p" "$BASE/.env" | tail -1
}

ping_url=$(env_value BACKUP_PING_URL)
ping() {
  [ -n "$ping_url" ] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "$ping_url$1" || echo "ping $ping_url$1 failed"
}
trap 'ping /fail' ERR

compose() {
  docker compose --env-file "$BASE/.env" -f docker-compose.prod.yml "$@"
}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$DEST/agntspark_gateway-$stamp.dump"

cd "$BASE/agntspark-gateway/deploy"
compose exec -T postgres \
  pg_dump -U agntspark -d agntspark_gateway --format=custom > "$target.partial"

# Restore it into a scratch database and make sure the data came back.
scratch=agntspark_restore_check
compose exec -T postgres dropdb -U agntspark --if-exists "$scratch"
compose exec -T postgres createdb -U agntspark "$scratch"
compose exec -T postgres pg_restore -U agntspark -d "$scratch" --no-owner --exit-on-error \
  < "$target.partial"
restored_users=$(compose exec -T postgres psql -U agntspark -d "$scratch" -tAc 'SELECT count(*) FROM users')
live_users=$(compose exec -T postgres psql -U agntspark -d agntspark_gateway -tAc 'SELECT count(*) FROM users')
compose exec -T postgres dropdb -U agntspark "$scratch"
# Accounts created during the dump may be missing from it, never the reverse.
if [ "$restored_users" -gt "$live_users" ] || { [ "$live_users" -gt 0 ] && [ "$restored_users" -eq 0 ]; }; then
  echo "restore check failed: $restored_users users restored, $live_users live" >&2
  false
fi
echo "restore check passed: $restored_users users"
mv "$target.partial" "$target"

find "$DEST" -name 'agntspark_gateway-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name '*.partial' -mmin +60 -delete
echo "backup written: $target ($(stat -c %s "$target") bytes)"

r2_account=$(env_value R2_ACCOUNT_ID)
r2_bucket=$(env_value R2_BUCKET)
if [ -z "$r2_account" ] || [ -z "$r2_bucket" ]; then
  echo "off-host copy skipped: R2_ACCOUNT_ID / R2_BUCKET not set"
  ping ""
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
ping ""
