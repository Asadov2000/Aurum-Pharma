#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
. "$script_dir/recovery-metrics.sh"

mode="${1:-}"
case "$mode" in
    full|wal) ;;
    *) echo "Usage: $0 full|wal" >&2; exit 64 ;;
esac

lock_file="${AURUM_BACKUP_LOCK_FILE:-/run/lock/aurum-recovery.lock}"
lock_wait_seconds="${AURUM_RECOVERY_LOCK_WAIT_SECONDS:-600}"
case "$lock_wait_seconds" in
    ""|*[!0-9]*) echo "Invalid recovery lock wait" >&2; exit 64 ;;
esac
exec 9>"$lock_file"
if ! flock -w "$lock_wait_seconds" 9; then
    echo "Timed out waiting for the Aurum recovery lock" >&2
    exit 75
fi

compose() {
    docker compose \
        --env-file /etc/aurum/production.env \
        --file docker-compose.production.yml \
        --file docker-compose.recovery.yml \
        "$@"
}

if [ "$mode" = "full" ]; then
    aurum_run_recovery_step \
        combined_backup compose --profile backup run --rm backup
    aurum_run_recovery_step \
        pitr_base_backup compose --profile backup run --rm pitr-basebackup
fi

aurum_run_recovery_step \
    wal_snapshot compose --profile backup run --rm wal-snapshot
aurum_run_recovery_step \
    offsite_export compose --profile offsite run --rm offsite-sync
