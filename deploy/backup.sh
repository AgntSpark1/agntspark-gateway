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
# These live on the same disk as the database: they cover mistakes and bad
# migrations, not losing the VM. Copy them off-host for that.
set -euo pipefail
umask 077

BASE=/opt/agntspark
DEST="$BASE/backups"
KEEP_DAYS=14
mkdir -p "$DEST"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$DEST/agntspark_gateway-$stamp.dump"

cd "$BASE/agntspark-gateway/deploy"
docker compose --env-file "$BASE/.env" -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U agntspark -d agntspark_gateway --format=custom > "$target.partial"
mv "$target.partial" "$target"

find "$DEST" -name 'agntspark_gateway-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name '*.partial' -mmin +60 -delete
echo "backup written: $target ($(stat -c %s "$target") bytes)"
