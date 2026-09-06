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

env_file="${AURUM_PRODUCTION_ENV_FILE:-/etc/aurum/production.env}"
production_project="${AURUM_PRODUCTION_COMPOSE_PROJECT:-aurum-production}"
drill_project="aurum-restore-$(date -u +%Y%m%dT%H%M%SZ)-$$"
case "$production_project" in
    ""|*[!A-Za-z0-9_-]*|[-_]*)
        echo "Invalid production Compose project name" >&2
        exit 64
        ;;
esac

production_compose() {
    docker compose \
        --project-name "$production_project" \
        --env-file "$env_file" \
        --file docker-compose.production.yml \
        --file docker-compose.recovery.yml \
        "$@"
}

drill_compose() {
    docker compose \
        --project-name "$drill_project" \
        --env-file "$env_file" \
        --file docker-compose.production.yml \
        --file docker-compose.recovery.yml \
        "$@"
}

target_name="aurum_drill_$(date -u +%Y%m%dT%H%M%SZ)_$$"
checkpoint="$(production_compose exec -T postgres psql \
    -v ON_ERROR_STOP=1 \
    -U postgres \
    -d aurum \
    --tuples-only \
    --no-align \
    --command "SELECT pg_create_restore_point('$target_name'); SELECT pg_walfile_name(pg_switch_wal());")"
target_lsn="$(printf '%s\n' "$checkpoint" | sed -n '1p' | tr -d '[:space:]')"
expected_wal="$(printf '%s\n' "$checkpoint" | tail -n 1 | tr -d '[:space:]')"
[ -n "$target_lsn" ] && [ -n "$expected_wal" ] || {
    echo "Could not create the PITR drill checkpoint" >&2
    exit 1
}
export AURUM_PITR_TARGET_NAME="$target_name"
export AURUM_PITR_TARGET_LSN="$target_lsn"

attempt=1
until production_compose exec -T postgres test -f "/wal-archive/$expected_wal.gz"; do
    if [ "$attempt" -ge 60 ]; then
        echo "PITR checkpoint WAL was not archived: $expected_wal" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 1
done
aurum_run_recovery_step \
    pitr_checkpoint_wal_snapshot \
    production_compose --profile backup run --rm wal-snapshot

cleanup() {
    status=$?
    set +e
    drill_compose --profile restore-drill down --volumes --remove-orphans >/dev/null 2>&1
    trap - EXIT INT TERM
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

aurum_run_recovery_step \
    logical_restore_drill \
    drill_compose --profile restore-drill run --rm restore-drill
aurum_run_recovery_step \
    pitr_restore_drill \
    drill_compose --profile restore-drill run --rm pitr-restore-drill

printf 'Local recovery drill completed: %s\n' "$drill_project"
