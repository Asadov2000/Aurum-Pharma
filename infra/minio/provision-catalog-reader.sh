#!/bin/sh
# One-shot IAM provisioning. Runs only with maintenance credentials, never in a worker.
set -eu
umask 077

secret_value() {
    value="$1"
    filename="$2"
    if [ -n "$value" ]; then
        printf '%s' "$value"
    else
        cat "/run/secrets/$filename"
    fi
}

root_user="$(secret_value "${MINIO_ROOT_USER:-}" MINIO_ROOT_USER)"
root_password="$(secret_value "${MINIO_ROOT_PASSWORD:-}" MINIO_ROOT_PASSWORD)"
catalog_user="$(secret_value "${CATALOG_MINIO_ACCESS_KEY:-}" CATALOG_MINIO_ACCESS_KEY)"
catalog_password="$(secret_value "${CATALOG_MINIO_SECRET_KEY:-}" CATALOG_MINIO_SECRET_KEY)"
app_user="${MINIO_ACCESS_KEY:-}"
if [ -z "$app_user" ] && [ -f /run/secrets/MINIO_ACCESS_KEY ]; then
    app_user="$(cat /run/secrets/MINIO_ACCESS_KEY)"
fi
backup_user="${MINIO_BACKUP_ACCESS_KEY:-}"
if [ -z "$backup_user" ] && [ -f /run/secrets/MINIO_BACKUP_ACCESS_KEY ]; then
    backup_user="$(cat /run/secrets/MINIO_BACKUP_ACCESS_KEY)"
fi
bucket="${MINIO_BUCKET:?MINIO_BUCKET is required}"
case "$bucket" in
    ''|*[!a-z0-9.-]*|.*|*.) echo 'Invalid catalog import bucket name' >&2; exit 1 ;;
esac
if [ "${#bucket}" -lt 3 ] || [ "${#bucket}" -gt 63 ]; then
    echo 'Invalid catalog import bucket length' >&2
    exit 1
fi
if [ -z "$catalog_user" ] || [ -z "$catalog_password" ] ||
    [ "$catalog_user" = "$root_user" ] || [ "$catalog_user" = "$app_user" ] ||
    [ "$catalog_user" = "$backup_user" ]; then
    echo 'Catalog storage must use an independent identity' >&2
    exit 1
fi
case "${MINIO_SECURE:-false}" in
    true) scheme=https ;;
    false) scheme=http ;;
    *) echo 'MINIO_SECURE must be true or false' >&2; exit 1 ;;
esac

attempt=0
until mc --config-dir /tmp/catalog-mc alias set local \
    "$scheme://${MINIO_ENDPOINT:?MINIO_ENDPOINT is required}" \
    "$root_user" "$root_password" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo 'Catalog storage provisioning could not connect' >&2
        exit 1
    fi
    sleep 2
done

# Adding a narrow policy does not remove existing direct or group permissions.
# Inspect before changing an existing password; unexpected privileges fail closed.
if mc --no-color --config-dir /tmp/catalog-mc admin user info \
    local "$catalog_user" >/tmp/catalog-user-info 2>/dev/null; then
    policy_seen=false
    groups_seen=false
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            PolicyName:*|MemberOf:*)
                value="${line#*:}"
                value="${value#"${value%%[![:space:]]*}"}"
                value="${value%"${value##*[![:space:]]}"}"
                case "$line" in
                    PolicyName:*)
                        policy_seen=true
                        case "$value" in
                            ''|aurum-catalog-reader) ;;
                            *) echo 'Catalog storage identity has unexpected policies' >&2; exit 1 ;;
                        esac ;;
                    MemberOf:*)
                        groups_seen=true
                        case "$value" in
                            '[]') ;;
                            *) echo 'Catalog storage identity has unexpected groups' >&2; exit 1 ;;
                        esac ;;
                esac ;;
        esac
    done </tmp/catalog-user-info
    if [ "$policy_seen" != true ] || [ "$groups_seen" != true ]; then
        echo 'Catalog storage identity inspection was incomplete' >&2
        exit 1
    fi
else
    info="$(mc --json --config-dir /tmp/catalog-mc admin user info \
        local "$catalog_user" 2>/dev/null || true)"
    case "$info" in
        *XMinioAdminNoSuchUser*) ;;
        *) echo 'Catalog storage identity could not be inspected' >&2; exit 1 ;;
    esac
fi

# No listing, metadata, mutations, version access or administrative actions.
# The worker explicitly supplies the configured S3 region for GetObject.
cat > /tmp/catalog-reader-policy.json <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject"],"Resource":["arn:aws:s3:::$bucket/*/imports/*"]}]}
EOF

# Idempotent provisioning preserves bucket contents and existing object versions.
# Suppress mc output because account commands can render access-key identities.
if ! (
    mc --config-dir /tmp/catalog-mc mb --ignore-existing "local/$bucket" &&
    mc --config-dir /tmp/catalog-mc admin user add local "$catalog_user" "$catalog_password" &&
    mc --config-dir /tmp/catalog-mc admin policy create \
        local aurum-catalog-reader /tmp/catalog-reader-policy.json &&
    mc --config-dir /tmp/catalog-mc admin policy attach \
        local aurum-catalog-reader --user "$catalog_user"
) >/dev/null 2>&1; then
    echo 'Catalog storage provisioning failed' >&2
    exit 1
fi
echo 'Catalog storage reader provisioned'
