#!/bin/sh
# DISASTER RECOVERY: restore a backup over the live database. Stop the application first:
#   docker compose stop backend worker beat
#   docker compose run --rm -e CONFIRM=yes backup sh /scripts/restore.sh /backups/pizza-YYYYMMDDTHHMMSSZ.dump
set -eu
FILE="${1:?usage: restore.sh <dump file>}"
: "${POSTGRES_DB:?}" "${POSTGRES_USER:?}"
[ "${CONFIRM:-}" = "yes" ] || { echo "Refusing to restore without CONFIRM=yes (this replaces $POSTGRES_DB)" >&2; exit 2; }
sha256sum -c "$FILE.sha256"
pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error --single-transaction "$FILE"
echo "Restored $FILE into $POSTGRES_DB. Run migrations, then start the application."
