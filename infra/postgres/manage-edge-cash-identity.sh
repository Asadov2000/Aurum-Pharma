#!/bin/sh
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

edge_node_id="$(printf '%s' "${EDGE_NODE_ID:-}" | tr 'A-F' 'a-f')"
identity_action="${EDGE_IDENTITY_ACTION:-enroll}"

printf '%s' "$edge_node_id" | grep -Eq \
    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' || {
    echo "EDGE_NODE_ID must be a UUID" >&2
    exit 1
}
case "$identity_action" in
    enroll|revoke) ;;
    *)
        echo "EDGE_IDENTITY_ACTION must be enroll or revoke" >&2
        exit 1
        ;;
esac

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PGPASSWORD="$POSTGRES_PASSWORD"

psql \
    -v ON_ERROR_STOP=1 \
    -v edge_node_id="$edge_node_id" \
    -v identity_action="$identity_action" \
    --single-transaction \
    --host "${POSTGRES_HOST:-postgres}" \
    --port "${POSTGRES_PORT:-5432}" \
    --username "${POSTGRES_USER:-postgres}" \
    --dbname "$POSTGRES_DB" \
    --file "$script_dir/manage-edge-cash-identity.sql"

unset PGPASSWORD POSTGRES_PASSWORD
