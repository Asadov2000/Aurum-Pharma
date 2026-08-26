#!/bin/sh
set -eu

mode="${1:-}"
case "$mode" in
    full|wal) ;;
    *) echo "Usage: $0 full|wal" >&2; exit 64 ;;
esac

lock_file="${AURUM_BACKUP_LOCK_FILE:-/run/lock/aurum-recovery.lock}"
exec 9>"$lock_file"
if ! flock -n 9; then
    echo "Another Aurum recovery cycle is active; this run is skipped"
    exit 0
fi

compose() {
    docker compose \
        --env-file /etc/aurum/production.env \
        --file docker-compose.production.yml \
        --file docker-compose.recovery.yml \
        "$@"
}

if [ "$mode" = "full" ]; then
    compose --profile backup run --rm backup
    compose --profile backup run --rm pitr-basebackup
fi

compose --profile backup run --rm wal-snapshot
compose --profile offsite run --rm offsite-sync
