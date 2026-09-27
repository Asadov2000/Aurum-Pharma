#!/bin/sh
# Synthetic read-only rehearsal only; never a production backup/restore entrypoint.
set -eu
umask 077

[ "${POSTGRES_HOST:-}" = edge-benchmark-postgres ] &&
    [ "${POSTGRES_DB:-}" = aurum_edge_read_benchmark_test ] &&
    [ "${POSTGRES_USER:-}" = postgres ] &&
    [ -n "${POSTGRES_PASSWORD:-}" ] && [ -s "${RESTIC_PASSWORD_FILE:-/missing}" ] || {
    echo "Refusing recovery outside the isolated synthetic Edge database." >&2
    exit 1
}

export PGPASSWORD="$POSTGRES_PASSWORD"
export RESTIC_REPOSITORY=/repository
export RESTIC_CACHE_DIR=/workspace/cache
export RESTIC_PASSWORD_FILE
export TMPDIR=/workspace/tmp
mkdir -p "$TMPDIR" "$RESTIC_CACHE_DIR" /payload /repository /transfer /corrupt /restored

case "${1:-}" in
    backup)
        pg_dump --host "$POSTGRES_HOST" --username "$POSTGRES_USER" \
            --dbname "$POSTGRES_DB" --format custom --file /payload/database.dump
        (cd /payload && sha256sum database.dump > database.dump.sha256)
        restic init >/dev/null
        # Preserve restic's exit status (including incomplete snapshot exit 3).
        restic backup --json --host aurum-edge-synthetic --tag read-only-drill /payload > /workspace/backup.json
        jq -r 'select(.message_type == "summary") | .snapshot_id' \
            /workspace/backup.json > /transfer/snapshot-id
        snapshot_id=$(cat /transfer/snapshot-id)
        [ "${#snapshot_id}" -eq 64 ] || { echo "Missing completed backup snapshot." >&2; exit 1; }
        restic check --read-data >/dev/null
        tar -cf /transfer/repository.tar -C /repository .
        echo "encrypted_edge_backup=verified"
        ;;
    restore)
        snapshot_id=${2:-}
        case "$snapshot_id" in
            *[!0-9a-f]*|'') echo "Invalid expected backup snapshot." >&2; exit 1 ;;
        esac
        [ "${#snapshot_id}" -eq 64 ] || { echo "Invalid expected backup snapshot." >&2; exit 1; }
        # The launcher transfers ciphertext only. Keys are supplied independently.
        tar -xf /transfer/repository.tar -C /repository
        restic check --read-data >/dev/null
        openssl rand -hex 32 > /workspace/wrong-key
        if RESTIC_PASSWORD_FILE=/workspace/wrong-key restic restore "$snapshot_id" \
            --target /workspace/wrong-key-output >/dev/null 2>&1; then
            echo "Encrypted repository accepted the wrong key." >&2
            exit 1
        fi
        echo "encrypted_edge_wrong_key=rejected"

        # Corrupt a separate ciphertext copy; do not weaken the valid repository.
        cp -a /repository/. /corrupt
        pack=$(find /corrupt/data -type f | sort | head -n 1)
        [ -n "$pack" ] || { echo "Encrypted repository has no data packs." >&2; exit 1; }
        # A zero-length pack is deterministic corruption, independent of byte values.
        # Restic creates immutable pack modes; change only the disposable copy.
        chmod u+w "$pack"
        : > "$pack"
        if RESTIC_REPOSITORY=/corrupt restic --no-cache check --read-data >/dev/null 2>&1; then
            echo "Encrypted repository accepted damaged ciphertext." >&2
            exit 1
        fi
        if RESTIC_REPOSITORY=/corrupt restic --no-cache restore "$snapshot_id" \
            --target /workspace/corrupt-output >/dev/null 2>&1; then
            echo "Restore accepted damaged ciphertext." >&2
            exit 1
        fi
        echo "encrypted_edge_corruption=rejected"

        restic --no-cache restore "$snapshot_id" --target /restored >/dev/null
        (cd /restored/payload && sha256sum -c database.dump.sha256 >/dev/null)
        # All integrity checks finish before the target database is touched.
        pg_restore --exit-on-error --clean --if-exists --host "$POSTGRES_HOST" \
            --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" /restored/payload/database.dump
        echo "encrypted_edge_restore=verified"
        ;;
    *) echo "Expected isolated backup or restore phase." >&2; exit 1 ;;
esac
