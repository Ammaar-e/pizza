#!/bin/sh
# Long-running backup scheduler for the backup container: one backup at start, then every interval,
# plus a weekly automated restore test.
set -u
INTERVAL="${BACKUP_INTERVAL_SECONDS:-21600}"
n=0
while true; do
  if sh /scripts/backup.sh; then
    n=$((n + 1))
    # Every 28th run (weekly at the default 6-hour interval) prove the latest backup restores.
    if [ $((n % 28)) -eq 1 ]; then sh /scripts/restore-test.sh || echo "RESTORE TEST FAILED" >&2; fi
  else
    echo "$(date -u) BACKUP FAILED" >&2
  fi
  sleep "$INTERVAL"
done
