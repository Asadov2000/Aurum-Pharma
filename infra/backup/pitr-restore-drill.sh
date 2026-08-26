#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp
export RESTIC_REPOSITORY=/repository
export RESTIC_PASSWORD_FILE=/run/secrets/RESTIC_PASSWORD

target_name="${AURUM_PITR_TARGET_NAME:-}"
case "$target_name" in
    *[!A-Za-z0-9_.:-]*) echo "Invalid PITR target name" >&2; exit 1 ;;
esac

drill=/scratch/pitr-drill
base_snapshot="$drill/base-snapshot"
wal_snapshot="$drill/wal-snapshot"
data="$drill/data"
socket="$drill/socket"
log="$drill/postgres.log"

rm -rf "$drill"
mkdir -p "$HOME" "$TMPDIR" "$base_snapshot" "$wal_snapshot" "$socket"
restic --no-lock restore latest --tag aurum-pitr-base --target "$base_snapshot"
restic --no-lock restore latest --tag aurum-wal --target "$wal_snapshot"

test -s "$base_snapshot/postgres-base/backup_manifest"
pg_verifybackup "$base_snapshot/postgres-base"
cp -a "$base_snapshot/postgres-base" "$data"
rm -f "$data/postmaster.pid"
touch "$data/recovery.signal"

cat >> "$data/postgresql.auto.conf" <<EOF
restore_command = 'gzip -dc $wal_snapshot/%f.gz > %p'
recovery_target_action = 'promote'
EOF
if [ -n "$target_name" ]; then
    printf "recovery_target_name = '%s'\n" "$target_name" >> "$data/postgresql.auto.conf"
fi

postgres \
    -D "$data" \
    -k "$socket" \
    -p 55432 \
    -c listen_addresses= \
    -c hba_file="$data/pg_hba.conf" \
    >"$log" 2>&1 &
postgres_pid=$!
cleanup() {
    set +e
    pg_ctl -D "$data" -m fast stop >/dev/null 2>&1 || true
    wait "$postgres_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

attempt=1
while ! pg_isready -q -h "$socket" -p 55432 -U postgres -d aurum; do
    if ! kill -0 "$postgres_pid" 2>/dev/null || [ "$attempt" -ge 60 ]; then
        tail -n 100 "$log" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 1
done

attempt=1
while :; do
    recovery_state="$(psql -h "$socket" -p 55432 -U postgres -d aurum \
        --tuples-only --no-align --command 'SELECT pg_is_in_recovery()')"
    [ "$recovery_state" = "f" ] && break
    if ! kill -0 "$postgres_pid" 2>/dev/null || [ "$attempt" -ge 60 ]; then
        tail -n 100 "$log" >&2
        echo "PITR server did not promote before the timeout" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 1
done
pg_amcheck --install-missing -h "$socket" -p 55432 -U postgres -d aurum

has_probe="$(psql -h "$socket" -p 55432 -U postgres -d aurum \
    --tuples-only --no-align \
    --command "SELECT to_regclass('public.pitr_probe') IS NOT NULL")"
if [ "$has_probe" = "t" ] && [ -n "$target_name" ]; then
    survived="$(psql -h "$socket" -p 55432 -U postgres -d aurum \
        --tuples-only --no-align \
        --command "SELECT count(*) FROM public.pitr_probe WHERE value = 'must_survive'")"
    excluded="$(psql -h "$socket" -p 55432 -U postgres -d aurum \
        --tuples-only --no-align \
        --command "SELECT count(*) FROM public.pitr_probe WHERE value = 'must_not_survive'")"
    test "$survived" = "1"
    test "$excluded" = "0"
fi

printf 'PITR restore drill passed (target: %s)\n' "${target_name:-latest}"
