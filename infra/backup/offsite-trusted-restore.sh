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
trusted_dir="${AURUM_TRUSTED_CHECKPOINT_DIR:-/trusted}"
public_key=/run/secrets/AURUM_RECOVERY_SIGNING_PUBLIC_KEY

aurum_validate_offsite_config "$endpoint" "$bucket" "$prefix" "$allow_insecure"
aurum_validate_export_id "$export_id"
[ -s "$public_key" ] || aurum_fail "Recovery signing public key is missing"

trusted_bundle="$trusted_dir/$export_id.trusted"
[ -d "$trusted_bundle" ] || aurum_fail "Explicit trusted checkpoint is missing"
mkdir -p "$HOME" "$TMPDIR" /workspace/restore
trusted_manifest=/workspace/restore/checkpoint.json
trusted_signature=/workspace/restore/checkpoint.sig
cp "$trusted_bundle/checkpoint.json" "$trusted_manifest"
cp "$trusted_bundle/checkpoint.sig" "$trusted_signature"
[ -s "$trusted_manifest" ] && [ -s "$trusted_signature" ] || \
    aurum_fail "Explicit trusted checkpoint is missing"
openssl pkeyutl -verify -rawin -pubin \
    -inkey "$public_key" -in "$trusted_manifest" -sigfile "$trusted_signature" \
    >/dev/null 2>&1 || aurum_fail "Trusted checkpoint signature verification failed"

signing_key_id="$(
    openssl pkey -pubin -in "$public_key" -outform DER 2>/dev/null | \
        sha256sum | awk '{print $1}'
)"
jq -e \
    --arg export_id "$export_id" \
    --arg bucket "$bucket" \
    --arg prefix "$prefix" \
    --arg key_id "$signing_key_id" \
    '.schema_version == 1 and .trust_domain == "aurum-offsite-recovery-v1" and
     .export_id == $export_id and .bucket == $bucket and .prefix == $prefix and
     .signing_algorithm == "ed25519-v1" and .signing_key_id == $key_id' \
    "$trusted_manifest" >/dev/null || aurum_fail "Trusted checkpoint scope is invalid"

access_key="$(aurum_read_secret AURUM_OFFSITE_RESTORE_ACCESS_KEY)"
secret_key="$(aurum_read_secret AURUM_OFFSITE_RESTORE_SECRET_KEY)"
run_root="$(mktemp -d /scratch/offsite-restore.XXXXXX)"
trap 'rm -rf "$run_root"' EXIT HUP INT TERM
repository="$run_root/repository"
restore="$run_root/restore"
mkdir -p "$repository" "$restore"
aurum_configure_offsite_alias /workspace/mc "$endpoint" "$access_key" "$secret_key"
aurum_require_worm_bucket /workspace/mc "$bucket"

object_map_key="$(jq -er '.repository_object_map.key' "$trusted_manifest")"
object_map_version_id="$(jq -er '.repository_object_map.version_id' "$trusted_manifest")"
object_map_sha256="$(jq -er '.repository_object_map.sha256' "$trusted_manifest")"
object_count="$(jq -er '.repository_object_map.object_count' "$trusted_manifest")"
[ "$object_map_key" = "$prefix/manifests/$export_id.objects.tsv" ] || \
    aurum_fail "Trusted object map key is invalid"
object_map=/workspace/restore/objects.tsv
mc --config-dir /workspace/mc cp \
    --version-id "$object_map_version_id" \
    "offsite/$bucket/$object_map_key" "$object_map" >/dev/null
[ "$(sha256sum "$object_map" | awk '{print $1}')" = "$object_map_sha256" ] || \
    aurum_fail "Trusted object map checksum verification failed"

aurum_download_exact_repository \
    /workspace/mc "$bucket" "$prefix" "$object_map" "$object_count" "$repository"
export RESTIC_REPOSITORY="$repository"
restic --no-lock check --read-data-subset=5%
pitr_snapshot_id="$(jq -er '.restic_snapshots.pitr_base' "$trusted_manifest")"
restic --no-lock restore "$pitr_snapshot_id" --target "$restore"
test -s "$restore/postgres-base/backup_manifest"
pg_verifybackup "$restore/postgres-base"

printf 'Trusted exact-version WORM restore passed: %s\n' "$export_id"
