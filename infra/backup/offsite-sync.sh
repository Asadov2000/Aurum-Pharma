#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp

. /opt/aurum/offsite-common.sh

endpoint="${AURUM_OFFSITE_ENDPOINT:?AURUM_OFFSITE_ENDPOINT is required}"
bucket="${AURUM_OFFSITE_BUCKET:?AURUM_OFFSITE_BUCKET is required}"
prefix="${AURUM_OFFSITE_PREFIX:-aurum-production}"
candidate_dir="${AURUM_OFFSITE_CANDIDATE_DIR:-/candidate}"
allow_insecure="${AURUM_OFFSITE_ALLOW_INSECURE:-false}"

aurum_validate_offsite_config "$endpoint" "$bucket" "$prefix" "$allow_insecure"

access_key="$(aurum_read_secret AURUM_OFFSITE_ACCESS_KEY)"
secret_key="$(aurum_read_secret AURUM_OFFSITE_SECRET_KEY)"
mkdir -p "$HOME" "$TMPDIR" /workspace/export "$candidate_dir"

aurum_configure_offsite_alias /workspace/mc "$endpoint" "$access_key" "$secret_key"
aurum_require_worm_bucket /workspace/mc "$bucket"

export_id="$(date -u +%Y%m%dT%H%M%SZ)-$PPID"
object_map="/workspace/export/$export_id.objects.tsv"
manifest_file="/workspace/export/$export_id.json"
pointer_file="$candidate_dir/$export_id.candidate.json"

mc --config-dir /workspace/mc mirror \
    --exclude 'locks/*' \
    /repository "offsite/$bucket/$prefix/repository" >/dev/null

(
    cd /repository
    find . -type f ! -path './locks/*' -print | LC_ALL=C sort
) | while IFS= read -r relative_path; do
    object_path="${relative_path#./}"
    case "$object_path" in
        ""|/*|*/|*//*|..|../*|*/../*|*/..|*\\*|*[!A-Za-z0-9._/-]*)
            aurum_fail "Unsafe local Restic repository path"
            ;;
    esac
    source_file="/repository/$object_path"
    version_id="$(aurum_latest_version_id \
        /workspace/mc "offsite/$bucket/$prefix/repository/$object_path")"
    sha256="$(sha256sum "$source_file" | awk '{print $1}')"
    size="$(wc -c < "$source_file" | tr -d ' ')"
    printf '%s\t%s\t%s\t%s\n' "$sha256" "$size" "$version_id" "$object_path"
done > "$object_map"

object_count="$(wc -l < "$object_map" | tr -d ' ')"
[ "$object_count" -gt 0 ] || aurum_fail "Local encrypted repository is empty"
object_map_sha256="$(sha256sum "$object_map" | awk '{print $1}')"
object_map_key="$prefix/manifests/$export_id.objects.tsv"
mc --config-dir /workspace/mc cp \
    "$object_map" "offsite/$bucket/$object_map_key" >/dev/null
object_map_version_id="$(aurum_latest_version_id \
    /workspace/mc "offsite/$bucket/$object_map_key")"

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq -S -n \
    --arg export_id "$export_id" \
    --arg created_at "$created_at" \
    --arg bucket "$bucket" \
    --arg prefix "$prefix" \
    --arg object_map_key "$object_map_key" \
    --arg object_map_version_id "$object_map_version_id" \
    --arg object_map_sha256 "$object_map_sha256" \
    --argjson object_count "$object_count" \
    '{
        schema_version: 2,
        export_id: $export_id,
        created_at_utc: $created_at,
        bucket: $bucket,
        prefix: $prefix,
        repository_object_count: $object_count,
        repository_object_map_key: $object_map_key,
        repository_object_map_version_id: $object_map_version_id,
        repository_object_map_sha256: $object_map_sha256,
        retention_mode: "COMPLIANCE"
    }' > "$manifest_file"

mc --config-dir /workspace/mc cp \
    "$manifest_file" "offsite/$bucket/$prefix/manifests/$export_id.json" >/dev/null
manifest_version_id="$(aurum_latest_version_id \
    /workspace/mc "offsite/$bucket/$prefix/manifests/$export_id.json")"
manifest_sha256="$(sha256sum "$manifest_file" | awk '{print $1}')"
mc --config-dir /workspace/mc cp \
    --version-id "$manifest_version_id" \
    "offsite/$bucket/$prefix/manifests/$export_id.json" \
    /workspace/export/verified.json >/dev/null
cmp "$manifest_file" /workspace/export/verified.json

pointer_tmp="$candidate_dir/.$export_id.candidate.json.tmp"
jq -S -n \
    --arg export_id "$export_id" \
    --arg manifest_key "$prefix/manifests/$export_id.json" \
    --arg manifest_version_id "$manifest_version_id" \
    --arg manifest_sha256 "$manifest_sha256" \
    '{
        schema_version: 1,
        export_id: $export_id,
        manifest_key: $manifest_key,
        manifest_version_id: $manifest_version_id,
        manifest_sha256: $manifest_sha256
    }' > "$pointer_tmp"
chmod 600 "$pointer_tmp"
mv "$pointer_tmp" "$pointer_file"

printf 'Off-site WORM candidate completed: %s (encrypted objects: %s)\n' \
    "$export_id" "$object_count"
