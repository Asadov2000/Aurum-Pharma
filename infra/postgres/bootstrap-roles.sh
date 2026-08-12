#!/bin/sh
# Idempotently creates and normalizes Aurum database roles on an existing DB.
set -eu

load_secret() {
    name="$1"
    file_name="${name}_FILE"
    value="$(printenv "$name" 2>/dev/null || true)"
    file_path="$(printenv "$file_name" 2>/dev/null || true)"

    if [ -n "$value" ] && [ -n "$file_path" ]; then
        echo "$name and $file_name cannot both be set" >&2
        exit 1
    fi
    if [ -n "$file_path" ]; then
        [ -f "$file_path" ] || {
            echo "$file_name does not point to a readable file" >&2
            exit 1
        }
        value="$(cat "$file_path")"
    fi
    [ -n "$value" ] || {
        echo "$name is required" >&2
        exit 1
    }
    export "$name=$value"
}

load_secret POSTGRES_PASSWORD
load_secret AURUM_APP_PASSWORD
load_secret AURUM_SUPPORT_PASSWORD
load_secret AURUM_MAILER_PASSWORD
load_secret AURUM_MIGRATOR_PASSWORD

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PGPASSWORD="$POSTGRES_PASSWORD"

postgres_host="${POSTGRES_HOST:-postgres}"
postgres_port="${POSTGRES_PORT:-5432}"
attempt=1
max_attempts=30

# The official image briefly accepts connections on its temporary init server
# before restarting PostgreSQL. Retry only connection failures; SQL errors must
# still fail immediately so a broken role contract cannot be hidden.
while :; do
    set +e
    psql \
        -v ON_ERROR_STOP=1 \
        --single-transaction \
        --host "$postgres_host" \
        --port "$postgres_port" \
        --username "${POSTGRES_USER:-postgres}" \
        --dbname "$POSTGRES_DB" \
        --file "$script_dir/role-contract.sql"
    status=$?
    set -e

    if [ "$status" -eq 0 ]; then
        break
    fi
    if [ "$status" -ne 2 ] || [ "$attempt" -ge "$max_attempts" ]; then
        exit "$status"
    fi

    attempt=$((attempt + 1))
    sleep 1
done

unset \
    PGPASSWORD \
    POSTGRES_PASSWORD \
    AURUM_APP_PASSWORD \
    AURUM_SUPPORT_PASSWORD \
    AURUM_MAILER_PASSWORD \
    AURUM_MIGRATOR_PASSWORD
