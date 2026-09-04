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
    '.schema_version == 2 and .trust_domain == "aurum-offsite-recovery-v1" and
     .export_id == $export_id and .bucket == $bucket and .prefix == $prefix and
     .signing_algorithm == "ed25519-v1" and .signing_key_id == $key_id and
     (.restic_snapshots.combined | type == "string" and length > 0) and
     (.restic_snapshots.pitr_base | type == "string" and length > 0) and
     (.restic_snapshots.wal | type == "string" and length > 0)' \
    "$trusted_manifest" >/dev/null || aurum_fail "Trusted checkpoint scope is invalid"

access_key="$(aurum_read_secret AURUM_OFFSITE_RESTORE_ACCESS_KEY)"
secret_key="$(aurum_read_secret AURUM_OFFSITE_RESTORE_SECRET_KEY)"
run_root="$(mktemp -d /scratch/offsite-restore.XXXXXX)"
trap 'rm -rf "$run_root"' EXIT HUP INT TERM
repository="$run_root/repository"
combined_restore="$run_root/combined"
pitr_restore="$run_root/pitr"
wal_restore="$run_root/wal"
mkdir -p "$repository" "$combined_restore" "$pitr_restore" "$wal_restore"
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
combined_snapshot_id="$(jq -er '.restic_snapshots.combined' "$trusted_manifest")"
pitr_snapshot_id="$(jq -er '.restic_snapshots.pitr_base' "$trusted_manifest")"
wal_snapshot_id="$(jq -er '.restic_snapshots.wal' "$trusted_manifest")"
restic --no-lock restore "$combined_snapshot_id" --target "$combined_restore"
restic --no-lock restore "$pitr_snapshot_id" --target "$pitr_restore"
restic --no-lock restore "$wal_snapshot_id" --target "$wal_restore"

combined_manifest="$(find "$combined_restore" -type f -name manifest.json -print -quit)"
[ -n "$combined_manifest" ] || aurum_fail "Combined backup manifest is missing"
combined_root="$(dirname "$combined_manifest")"
jq -e '.schema_version == 1 and .consistency_mode ==
    "postgres-consistent-dump-plus-current-object-snapshot"' \
    "$combined_manifest" >/dev/null || aurum_fail "Combined backup manifest is invalid"
expected_dump_sha256="$(jq -er '.database_dump_sha256' "$combined_manifest")"
[ "$(sha256sum "$combined_root/database.dump" | awk '{print $1}')" = \
    "$expected_dump_sha256" ] || aurum_fail "Combined database dump checksum failed"
[ -f "$combined_root/minio-files.sha256" ] || \
    aurum_fail "Combined object checksum manifest is missing"
(
    cd "$combined_root/minio"
    sha256sum -c "$combined_root/minio-files.sha256" >/dev/null
) || aurum_fail "Combined object checksum verification failed"

test -s "$pitr_restore/postgres-base/backup_manifest"
pg_verifybackup "$pitr_restore/postgres-base"
find "$wal_restore" -type f -name '*.gz' -print -quit | grep -q . || \
    aurum_fail "Trusted WAL snapshot is empty"

printf 'Trusted exact-version WORM payload verification passed: %s\n' "$export_id"
