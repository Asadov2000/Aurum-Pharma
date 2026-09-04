#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp

. /opt/aurum/offsite-common.sh

export_id="${AURUM_OFFSITE_EXPORT_ID:?AURUM_OFFSITE_EXPORT_ID is required}"
verified_dir="${AURUM_VERIFIED_CHECKPOINT_DIR:-/verified}"
authorization_dir="${AURUM_SIGNING_AUTHORIZATION_DIR:-/authorization}"
trusted_dir="${AURUM_TRUSTED_CHECKPOINT_DIR:-/trusted}"
private_key=/run/secrets/AURUM_RECOVERY_SIGNING_PRIVATE_KEY

aurum_validate_export_id "$export_id"
[ -s "$private_key" ] || aurum_fail "Recovery signing private key is missing"
mkdir -p "$HOME" "$TMPDIR" /workspace/sign "$trusted_dir"

verified_source="$verified_dir/$export_id.verified.json"
[ -s "$verified_source" ] || aurum_fail "Verified checkpoint is missing"
verified=/workspace/sign/checkpoint.json
cp "$verified_source" "$verified"
verified_sha256="$(sha256sum "$verified" | awk '{print $1}')"
authorization="$authorization_dir/$export_id.authorize.sha256"
[ -s "$authorization" ] || aurum_fail "Independent signing authorization is missing"
authorized_sha256="$(tr -d ' \r\n\t' < "$authorization")"
case "$authorized_sha256" in
    *[!0-9a-f]*|"") aurum_fail "Signing authorization digest is invalid" ;;
esac
[ "${#authorized_sha256}" -eq 64 ] || \
    aurum_fail "Signing authorization digest is invalid"
[ "$verified_sha256" = "$authorized_sha256" ] || \
    aurum_fail "Verified checkpoint is not authorized for signing"

public_key=/workspace/sign/public.pem
openssl pkey -in "$private_key" -pubout -out "$public_key" >/dev/null 2>&1
signing_key_id="$(
    openssl pkey -pubin -in "$public_key" -outform DER 2>/dev/null | \
        sha256sum | awk '{print $1}'
)"
jq -e \
    --arg export_id "$export_id" \
    --arg key_id "$signing_key_id" \
    '.schema_version == 2 and .trust_domain == "aurum-offsite-recovery-v1" and
     .export_id == $export_id and .signing_algorithm == "ed25519-v1" and
     .signing_key_id == $key_id and
     (.restic_snapshots.combined | type == "string" and length > 0) and
     (.restic_snapshots.pitr_base | type == "string" and length > 0) and
     (.restic_snapshots.wal | type == "string" and length > 0)' \
    "$verified" >/dev/null || aurum_fail "Verified checkpoint scope is invalid"

signature=/workspace/sign/checkpoint.sig
openssl pkeyutl -sign -rawin \
    -inkey "$private_key" -in "$verified" -out "$signature"

bundle="$trusted_dir/$export_id.trusted"
sign_lock="$trusted_dir/.$export_id.sign.lock"
mkdir "$sign_lock" 2>/dev/null || \
    aurum_fail "Another signer is already processing this export ID"
bundle_tmp="$trusted_dir/.$export_id.trusted.tmp.$$"
trap 'rm -rf "$bundle_tmp" "$sign_lock"' EXIT HUP INT TERM
[ ! -e "$bundle" ] || aurum_fail "Trusted checkpoint already exists and is immutable"
mkdir "$bundle_tmp"
cp "$verified" "$bundle_tmp/checkpoint.json"
cp "$signature" "$bundle_tmp/checkpoint.sig"
chmod 700 "$bundle_tmp"
chmod 600 "$bundle_tmp/checkpoint.json" "$bundle_tmp/checkpoint.sig"
mv "$bundle_tmp" "$bundle"

printf 'Trusted off-site checkpoint signed offline: %s (key: %s)\n' \
    "$export_id" "$signing_key_id"
