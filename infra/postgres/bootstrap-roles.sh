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
load_secret AURUM_MIGRATOR_PASSWORD

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PGPASSWORD="$POSTGRES_PASSWORD"

psql \
    -v ON_ERROR_STOP=1 \
    --single-transaction \
    --host "${POSTGRES_HOST:-postgres}" \
    --port "${POSTGRES_PORT:-5432}" \
    --username "${POSTGRES_USER:-postgres}" \
    --dbname "$POSTGRES_DB" \
    --file "$script_dir/role-contract.sql"

unset \
    PGPASSWORD \
    POSTGRES_PASSWORD \
    AURUM_APP_PASSWORD \
    AURUM_SUPPORT_PASSWORD \
    AURUM_MIGRATOR_PASSWORD
