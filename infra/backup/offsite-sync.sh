#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp

read_secret() {
    value="$(cat "/run/secrets/$1")"
    [ -n "$value" ] || {
        echo "Required off-site secret is empty: $1" >&2
        exit 1
    }
    printf '%s' "$value"
}

endpoint="${AURUM_OFFSITE_ENDPOINT:?AURUM_OFFSITE_ENDPOINT is required}"
bucket="${AURUM_OFFSITE_BUCKET:?AURUM_OFFSITE_BUCKET is required}"
prefix="${AURUM_OFFSITE_PREFIX:-aurum-production}"

case "$endpoint" in
    https://*) ;;
    http://*)
        [ "${AURUM_OFFSITE_ALLOW_INSECURE:-false}" = "true" ] || {
            echo "Off-site endpoint must use HTTPS" >&2
            exit 1
        }
        ;;
    *) echo "Off-site endpoint must be an HTTP(S) URL" >&2; exit 1 ;;
esac
case "$bucket" in
    ""|*[!a-z0-9.-]*) echo "Invalid off-site bucket name" >&2; exit 1 ;;
esac
case "$prefix" in
    ""|/*|*/|*..*|*[!A-Za-z0-9._/-]*)
        echo "Invalid off-site prefix" >&2
        exit 1
        ;;
esac

access_key="$(read_secret AURUM_OFFSITE_ACCESS_KEY)"
secret_key="$(read_secret AURUM_OFFSITE_SECRET_KEY)"
mkdir -p "$HOME" "$TMPDIR" /workspace/export

mc --config-dir /workspace/mc alias set \
    offsite "$endpoint" "$access_key" "$secret_key" >/dev/null
retention="$(mc --config-dir /workspace/mc retention info "offsite/$bucket")"
printf '%s\n' "$retention" | grep -q 'COMPLIANCE' || {
    echo "Off-site bucket must have default COMPLIANCE Object Lock" >&2
    exit 1
}
versioning="$(mc --config-dir /workspace/mc version info "offsite/$bucket")"
printf '%s\n' "$versioning" | grep -qi 'enabled' || {
    echo "Off-site bucket versioning must be enabled" >&2
    exit 1
}

export_id="$(date -u +%Y%m%dT%H%M%SZ)-$PPID"
checksum_file="/workspace/export/$export_id.sha256"
manifest_file="/workspace/export/$export_id.json"
(
    cd /repository
    find . -type f ! -path './locks/*' -exec sha256sum '{}' \; | sort > "$checksum_file"
)
object_count="$(wc -l < "$checksum_file" | tr -d ' ')"
[ "$object_count" -gt 0 ] || {
    echo "Local encrypted repository is empty" >&2
    exit 1
}

mc --config-dir /workspace/mc mirror \
    --exclude 'locks/*' \
    /repository "offsite/$bucket/$prefix/repository" >/dev/null
checksum_sha256="$(sha256sum "$checksum_file" | awk '{print $1}')"
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$manifest_file" <<EOF
{
  "schema_version": 1,
  "export_id": "$export_id",
  "created_at_utc": "$created_at",
  "repository_object_count": $object_count,
  "repository_checksum_manifest": "$export_id.sha256",
  "repository_checksum_manifest_sha256": "$checksum_sha256",
  "retention_mode": "COMPLIANCE"
}
EOF

mc --config-dir /workspace/mc cp \
    "$checksum_file" "offsite/$bucket/$prefix/manifests/$export_id.sha256" >/dev/null
mc --config-dir /workspace/mc cp \
    "$manifest_file" "offsite/$bucket/$prefix/manifests/$export_id.json" >/dev/null
mc --config-dir /workspace/mc cp \
    "offsite/$bucket/$prefix/manifests/$export_id.json" \
    /workspace/export/verified.json >/dev/null
cmp "$manifest_file" /workspace/export/verified.json

printf 'Off-site WORM export completed: %s (encrypted objects: %s)\n' \
    "$export_id" "$object_count"
