#!/bin/sh
# Create a compressed, consistent PostgreSQL backup (pg_dump custom format) and prune old ones.
# Runs inside the backup container (PGHOST/PGPASSWORD set) or on the host with libpq tools installed.
set -eu
: "${POSTGRES_DB:?}" "${POSTGRES_USER:?}"
BACKUP_DIR="${BACKUP_DIR_IN_CONTAINER:-/backups}"
RETENTION="${BACKUP_RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/${POSTGRES_DB}-${STAMP}.dump"

mkdir -p "$BACKUP_DIR"
pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=6 --no-owner --file="$OUT.partial"
# Verify the archive is readable before publishing it.
pg_restore --list "$OUT.partial" > /dev/null
mv "$OUT.partial" "$OUT"
sha256sum "$OUT" > "$OUT.sha256"
echo "$(date -u) backup ok: $OUT ($(du -h "$OUT" | cut -f1))"

# Retention: delete dumps older than N days (only files this script created).
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name "${POSTGRES_DB}-*.dump" -o -name "${POSTGRES_DB}-*.dump.sha256" \) -mtime +"$RETENTION" -print -delete
