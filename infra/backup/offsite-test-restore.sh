#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp
export RESTIC_PASSWORD_FILE=/run/secrets/RESTIC_PASSWORD

root_user="$(cat /run/secrets/MINIO_ROOT_USER)"
root_password="$(cat /run/secrets/MINIO_ROOT_PASSWORD)"
test -n "$root_user"
test -n "$root_password"

repository=/scratch/offsite-repository
restore=/scratch/offsite-restore
rm -rf "$repository" "$restore"
mkdir -p "$HOME" "$TMPDIR" "$repository" "$restore"

mc --config-dir /workspace/mc alias set \
    offsite http://offsite-test-minio:9000 "$root_user" "$root_password" >/dev/null
mc --config-dir /workspace/mc mirror \
    offsite/aurum-offsite-test/aurum-ci/repository "$repository" >/dev/null

export RESTIC_REPOSITORY="$repository"
restic --no-lock check --read-data-subset=5%
restic --no-lock restore latest --tag aurum-pitr-base --target "$restore"
test -s "$restore/postgres-base/backup_manifest"
pg_verifybackup "$restore/postgres-base"

printf 'Off-site WORM restore drill passed\n'
