#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp
export RESTIC_PASSWORD_FILE=/run/secrets/RESTIC_PASSWORD

. /opt/aurum/offsite-common.sh

endpoint="${AURUM_OFFSITE_ENDPOINT:?AURUM_OFFSITE_ENDPOINT is required}"
bucket="${AURUM_OFFSITE_BUCKET:?AURUM_OFFSITE_BUCKET is required}"
prefix="${AURUM_OFFSITE_PREFIX:-aurum-production}"
export_id="${AURUM_OFFSITE_EXPORT_ID:?AURUM_OFFSITE_EXPORT_ID is required}"
allow_insecure="${AURUM_OFFSITE_ALLOW_INSECURE:-false}"
approval_dir="${AURUM_OFFSITE_APPROVAL_DIR:-/approval}"
verified_dir="${AURUM_VERIFIED_CHECKPOINT_DIR:-/verified}"
public_key=/run/secrets/AURUM_RECOVERY_SIGNING_PUBLIC_KEY

aurum_validate_offsite_config "$endpoint" "$bucket" "$prefix" "$allow_insecure"
aurum_validate_export_id "$export_id"
[ -s "$public_key" ] || aurum_fail "Recovery signing public key is missing"

access_key="$(aurum_read_secret AURUM_OFFSITE_RESTORE_ACCESS_KEY)"
secret_key="$(aurum_read_secret AURUM_OFFSITE_RESTORE_SECRET_KEY)"
run_root="$(mktemp -d /scratch/offsite-trust.XXXXXX)"
trap 'rm -rf "$run_root"' EXIT HUP INT TERM
repository="$run_root/repository"
mkdir -p "$HOME" "$TMPDIR" /workspace/trust "$repository" "$verified_dir"
aurum_configure_offsite_alias /workspace/mc "$endpoint" "$access_key" "$secret_key"
aurum_require_worm_bucket /workspace/mc "$bucket"

# This approval is created on the independent trust host from the provider
# control plane. The production candidate directory is deliberately not mounted.
approval_source="$approval_dir/$export_id.approval.json"
[ -s "$approval_source" ] || aurum_fail "Independent off-site approval is missing"
approval=/workspace/trust/approval.json
cp "$approval_source" "$approval"
jq -e \
    --arg export_id "$export_id" \
    --arg key "$prefix/manifests/$export_id.json" \
    '.schema_version == 1 and .export_id == $export_id and
     .manifest_key == $key' \
    "$approval" >/dev/null || aurum_fail "Independent approval identity is invalid"

manifest_key="$(jq -er '.manifest_key' "$approval")"
manifest_version_id="$(jq -er '.manifest_version_id' "$approval")"
manifest_sha256="$(jq -er '.manifest_sha256' "$approval")"
manifest=/workspace/trust/candidate.json
mc --config-dir /workspace/mc cp \
    --version-id "$manifest_version_id" \
    "offsite/$bucket/$manifest_key" "$manifest" >/dev/null
[ "$(sha256sum "$manifest" | awk '{print $1}')" = "$manifest_sha256" ] || \
    aurum_fail "Candidate manifest checksum verification failed"

jq -e \
    --arg export_id "$export_id" \
    --arg bucket "$bucket" \
    --arg prefix "$prefix" \
    '.schema_version == 2 and .export_id == $export_id and
     .bucket == $bucket and .prefix == $prefix and
     .retention_mode == "COMPLIANCE"' \
    "$manifest" >/dev/null || aurum_fail "Candidate manifest scope is invalid"

object_map_key="$(jq -er '.repository_object_map_key' "$manifest")"
object_map_version_id="$(jq -er '.repository_object_map_version_id' "$manifest")"
object_map_sha256="$(jq -er '.repository_object_map_sha256' "$manifest")"
object_count="$(jq -er '.repository_object_count' "$manifest")"
[ "$object_map_key" = "$prefix/manifests/$export_id.objects.tsv" ] || \
    aurum_fail "Candidate object map key is invalid"
object_map=/workspace/trust/objects.tsv
mc --config-dir /workspace/mc cp \
    --version-id "$object_map_version_id" \
    "offsite/$bucket/$object_map_key" "$object_map" >/dev/null
[ "$(sha256sum "$object_map" | awk '{print $1}')" = "$object_map_sha256" ] || \
    aurum_fail "Candidate object map checksum verification failed"

aurum_download_exact_repository \
    /workspace/mc "$bucket" "$prefix" "$object_map" "$object_count" "$repository"
export RESTIC_REPOSITORY="$repository"
restic --no-lock check --read-data

combined_snapshot_id="$(
    restic --no-lock snapshots --tag aurum-combined --latest 1 --json | jq -er '.[0].id'
)"
pitr_snapshot_id="$(
    restic --no-lock snapshots --tag aurum-pitr-base --latest 1 --json | jq -er '.[0].id'
)"
wal_snapshot_id="$(
    restic --no-lock snapshots --tag aurum-wal --latest 1 --json | jq -er '.[0].id'
)"
signing_key_id="$(
    openssl pkey -pubin -in "$public_key" -outform DER 2>/dev/null | \
        sha256sum | awk '{print $1}'
)"
approved_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
verified_manifest="$verified_dir/$export_id.verified.json"
verify_lock="$verified_dir/.$export_id.verify.lock"
mkdir "$verify_lock" 2>/dev/null || \
    aurum_fail "Another verifier is already processing this export ID"
verified_tmp="$verified_dir/.$export_id.verified.tmp.$$"
trap 'rm -rf "$run_root" "$verify_lock"; rm -f "$verified_tmp"' EXIT HUP INT TERM
[ ! -e "$verified_manifest" ] || \
    aurum_fail "Verified checkpoint already exists and is immutable"

jq -S -n \
    --arg export_id "$export_id" \
    --arg approved_at "$approved_at" \
    --arg bucket "$bucket" \
    --arg prefix "$prefix" \
    --arg manifest_key "$manifest_key" \
    --arg manifest_version_id "$manifest_version_id" \
    --arg manifest_sha256 "$manifest_sha256" \
    --arg object_map_key "$object_map_key" \
    --arg object_map_version_id "$object_map_version_id" \
    --arg object_map_sha256 "$object_map_sha256" \
    --argjson object_count "$object_count" \
    --arg combined_snapshot_id "$combined_snapshot_id" \
    --arg pitr_snapshot_id "$pitr_snapshot_id" \
    --arg wal_snapshot_id "$wal_snapshot_id" \
    --arg signing_key_id "$signing_key_id" \
    '{
        schema_version: 2,
        trust_domain: "aurum-offsite-recovery-v1",
        export_id: $export_id,
        approved_at_utc: $approved_at,
        bucket: $bucket,
        prefix: $prefix,
        candidate_manifest: {
            key: $manifest_key,
            version_id: $manifest_version_id,
            sha256: $manifest_sha256
        },
        repository_object_map: {
            key: $object_map_key,
            version_id: $object_map_version_id,
            sha256: $object_map_sha256,
            object_count: $object_count
        },
        restic_snapshots: {
            combined: $combined_snapshot_id,
            pitr_base: $pitr_snapshot_id,
            wal: $wal_snapshot_id
        },
        signing_algorithm: "ed25519-v1",
        signing_key_id: $signing_key_id
    }' > "$verified_tmp"
chmod 600 "$verified_tmp"
mv "$verified_tmp" "$verified_manifest"

printf 'Exact WORM checkpoint verified for offline signing: %s\n' "$export_id"
