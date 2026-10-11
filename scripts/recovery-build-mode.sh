#!/usr/bin/env sh
# Sourced by the recovery drill; no resources are created by these guards.

aurum_recovery_validate_build_mode() (
    case "${AURUM_RECOVERY_USE_PREBUILT-false}" in
        false) return 0 ;;
        true) ;;
        *) echo "Invalid recovery prebuilt flag; expected true or false" >&2; return 1 ;;
    esac
    if [ "${CI:-}" != true ] || [ "${CIRCLECI:-}" != true ]; then
        echo "Prebuilt recovery images require an explicit CircleCI environment" >&2
        return 1
    fi
    source_id=$(docker image inspect --format '{{.Id}}' aurum-backup:ci 2>/dev/null) || {
        echo "Missing scanned CI backup image" >&2
        return 1
    }
    case "$source_id" in
        sha256:*) source_hash=${source_id#sha256:} ;;
        *) echo "Invalid scanned CI backup image ID" >&2; return 1 ;;
    esac
    if [ "${#source_hash}" -ne 64 ]; then
        echo "Invalid scanned CI backup image ID" >&2
        return 1
    fi
    case "$source_hash" in
        *[!0-9a-f]*) echo "Invalid scanned CI backup image ID" >&2; return 1 ;;
    esac
    recovery_id=$(docker image inspect --format '{{.Id}}' aurum-pharma-backup:ci 2>/dev/null) || {
        echo "Missing recovery CI backup image" >&2
        return 1
    }
    if [ "$recovery_id" != "$source_id" ]; then
        echo "Recovery and scanned CI backup image IDs do not match" >&2
        return 1
    fi
)

aurum_recovery_prepare_backup() {
    aurum_recovery_validate_build_mode || return 1
    if [ "${AURUM_RECOVERY_USE_PREBUILT-false}" = false ]; then
        compose --profile backup build backup
    fi
}
