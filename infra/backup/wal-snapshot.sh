#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp
export RESTIC_REPOSITORY=/repository
export RESTIC_PASSWORD_FILE=/run/secrets/RESTIC_PASSWORD

mkdir -p "$HOME" "$TMPDIR"
[ -d /wal-archive ] || {
    echo "WAL archive is not mounted" >&2
    exit 1
}

if [ ! -f /repository/config ]; then
    restic init
fi
restic snapshots >/dev/null
snapshot_id="$(date -u +%Y%m%dT%H%M%SZ)-$PPID"
(
    cd /wal-archive
    restic backup \
        --host aurum-production \
        --tag aurum-wal \
        --tag "$snapshot_id" \
        .
)

printf 'Encrypted WAL snapshot completed: %s\n' "$snapshot_id"
