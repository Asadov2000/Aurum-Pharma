#!/bin/sh

aurum_fail() {
    echo "$1" >&2
    exit 1
}

aurum_read_secret() {
    secret_path="/run/secrets/$1"
    [ -f "$secret_path" ] || aurum_fail "Required secret is missing: $1"
    value="$(cat "$secret_path")"
    [ -n "$value" ] || aurum_fail "Required secret is empty: $1"
    printf '%s' "$value"
}

aurum_validate_offsite_config() {
    endpoint="$1"
    bucket="$2"
    prefix="$3"
    allow_insecure="$4"

    case "$endpoint" in
        https://*) ;;
        http://*)
            [ "$allow_insecure" = "true" ] || \
                aurum_fail "Off-site endpoint must use HTTPS"
            ;;
        *) aurum_fail "Off-site endpoint must be an HTTP(S) URL" ;;
    esac
    case "$bucket" in
        ""|*[!a-z0-9.-]*) aurum_fail "Invalid off-site bucket name" ;;
    esac
    case "$prefix" in
        ""|/*|*/|*..*|*[!A-Za-z0-9._/-]*)
            aurum_fail "Invalid off-site prefix"
            ;;
    esac
}

aurum_configure_offsite_alias() {
    config_dir="$1"
    endpoint="$2"
    access_key="$3"
    secret_key="$4"

    mkdir -p "$config_dir"
    mc --config-dir "$config_dir" alias set \
        offsite "$endpoint" "$access_key" "$secret_key" >/dev/null
}

aurum_require_worm_bucket() {
    config_dir="$1"
    bucket="$2"
    retention="$(mc --config-dir "$config_dir" retention info "offsite/$bucket")"
    printf '%s\n' "$retention" | grep -q 'COMPLIANCE' || \
        aurum_fail "Off-site bucket must have default COMPLIANCE Object Lock"
    versioning="$(mc --config-dir "$config_dir" version info "offsite/$bucket")"
    printf '%s\n' "$versioning" | grep -qi 'enabled' || \
        aurum_fail "Off-site bucket versioning must be enabled"
}

aurum_latest_version_id() {
    config_dir="$1"
    object="$2"
    mc --json --config-dir "$config_dir" stat "$object" | \
        jq -er 'select(.status == "success") | .versionID // .versionId'
}

aurum_validate_export_id() {
    export_id="$1"
    case "$export_id" in
        ""|*[!0-9TZ-]*|*--*) aurum_fail "Invalid off-site export ID" ;;
    esac
    [ "${#export_id}" -le 80 ] || aurum_fail "Invalid off-site export ID"
}

aurum_validate_object_map() {
    map_file="$1"
    expected_count="$2"

    case "$expected_count" in
        ""|*[!0-9]*) aurum_fail "Invalid repository object count" ;;
    esac
    actual_count="$(wc -l < "$map_file" | tr -d ' ')"
    [ "$actual_count" = "$expected_count" ] || \
        aurum_fail "Repository object map count does not match checkpoint"
    awk -F '\t' '
        NF != 4 || seen[$4]++ { exit 1 }
    ' "$map_file" || aurum_fail "Repository object map is malformed or duplicated"
}

aurum_download_exact_repository() {
    config_dir="$1"
    bucket="$2"
    prefix="$3"
    map_file="$4"
    expected_count="$5"
    repository="$6"

    aurum_validate_object_map "$map_file" "$expected_count"
    [ -d "$repository" ] || aurum_fail "Exact restore destination is missing"
    [ -z "$(find "$repository" -mindepth 1 -print -quit)" ] || \
        aurum_fail "Exact restore destination must be empty"
    download_root="$(mktemp -d "$(dirname "$repository")/offsite-download.XXXXXX")"

    tab="$(printf '\t')"
    while IFS="$tab" read -r expected_sha expected_size version_id object_path; do
        case "$expected_sha" in
            *[!0-9a-f]*|"") aurum_fail "Invalid SHA-256 in repository object map" ;;
        esac
        [ "${#expected_sha}" -eq 64 ] || \
            aurum_fail "Invalid SHA-256 in repository object map"
        case "$expected_size" in
            ""|*[!0-9]*) aurum_fail "Invalid size in repository object map" ;;
        esac
        case "$version_id" in
            ""|null|*[!A-Za-z0-9._~+=/-]*)
                aurum_fail "Invalid WORM version ID in repository object map"
                ;;
        esac
        [ "${#version_id}" -le 1024 ] || \
            aurum_fail "Invalid WORM version ID in repository object map"
        case "$object_path" in
            ""|/*|*/|*//*|.|./*|*/./*|*/.|..|../*|*/../*|*/..|*\\*|*[!A-Za-z0-9._/-]*)
                aurum_fail "Unsafe repository object path in checkpoint"
                ;;
        esac

        destination="$repository/$object_path"
        download="$download_root/$object_path"
        mkdir -p "$(dirname "$destination")"
        mkdir -p "$(dirname "$download")"
        mc --config-dir "$config_dir" cp \
            --version-id "$version_id" \
            "offsite/$bucket/$prefix/repository/$object_path" \
            "$download" >/dev/null
        actual_size="$(wc -c < "$download" | tr -d ' ')"
        [ "$actual_size" = "$expected_size" ] || \
            aurum_fail "Exact WORM object size verification failed"
        actual_sha="$(sha256sum "$download" | awk '{print $1}')"
        [ "$actual_sha" = "$expected_sha" ] || \
            aurum_fail "Exact WORM object checksum verification failed"
        mv "$download" "$destination"
    done < "$map_file"
    rm -rf "$download_root"
}
