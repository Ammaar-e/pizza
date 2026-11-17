#!/bin/sh
# Restoration test: restore the newest backup into a throwaway database, run sanity queries,
# then drop it. Never touches the live database.
set -eu
: "${POSTGRES_DB:?}" "${POSTGRES_USER:?}"
BACKUP_DIR="${BACKUP_DIR_IN_CONTAINER:-/backups}"
LATEST="$(ls -1t "$BACKUP_DIR/${POSTGRES_DB}"-*.dump 2>/dev/null | head -n1)"
[ -n "$LATEST" ] || { echo "no backups found" >&2; exit 1; }
sha256sum -c "$LATEST.sha256" >/dev/null
TEST_DB="restore_test_$(date +%s)"

cleanup() { dropdb -U "$POSTGRES_USER" --if-exists "$TEST_DB" || true; }
trap cleanup EXIT

createdb -U "$POSTGRES_USER" "$TEST_DB"
pg_restore -U "$POSTGRES_USER" -d "$TEST_DB" --no-owner --exit-on-error "$LATEST"

q() { psql -U "$POSTGRES_USER" -d "$TEST_DB" -Atc "$1"; }
MIG="$(q "select count(*) from django_migrations")"
STORES="$(q "select count(*) from organization_store")"
ORDERS="$(q "select count(*) from orders_order")"
[ "$MIG" -gt 0 ] || { echo "restore test: no migrations table rows" >&2; exit 1; }
echo "$(date -u) restore test OK from $(basename "$LATEST"): migrations=$MIG stores=$STORES orders=$ORDERS"
