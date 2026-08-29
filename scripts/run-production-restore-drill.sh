#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
. "$script_dir/recovery-metrics.sh"

lock_file="${AURUM_BACKUP_LOCK_FILE:-/run/lock/aurum-recovery.lock}"
lock_wait_seconds="${AURUM_RECOVERY_LOCK_WAIT_SECONDS:-7200}"
case "$lock_wait_seconds" in
    ""|*[!0-9]*) echo "Invalid recovery lock wait" >&2; exit 64 ;;
esac
exec 9>"$lock_file"
if ! flock -w "$lock_wait_seconds" 9; then
    echo "Timed out waiting for the Aurum recovery lock" >&2
    exit 75
fi

project="aurum-restore-$(date -u +%Y%m%dT%H%M%SZ)-$$"
compose() {
    docker compose \
        --project-name "$project" \
        --env-file /etc/aurum/production.env \
        --file docker-compose.production.yml \
        --file docker-compose.recovery.yml \
        "$@"
}

cleanup() {
    status=$?
    set +e
    compose --profile restore-drill down --volumes --remove-orphans >/dev/null 2>&1
    trap - EXIT INT TERM
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

aurum_run_recovery_step \
    logical_restore_drill \
    compose --profile restore-drill run --rm restore-drill
aurum_run_recovery_step \
    pitr_restore_drill \
    compose --profile restore-drill run --rm pitr-restore-drill

printf 'Local recovery drill completed: %s\n' "$project"
