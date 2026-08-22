#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp
export RESTIC_REPOSITORY=/repository
export RESTIC_PASSWORD_FILE=/run/secrets/RESTIC_PASSWORD

postgres_password="$(cat /run/secrets/POSTGRES_PASSWORD)"
app_password="$(cat /run/secrets/AURUM_APP_PASSWORD)"
support_password="$(cat /run/secrets/AURUM_SUPPORT_PASSWORD)"
backup_password="$(cat /run/secrets/AURUM_BACKUP_PASSWORD)"
minio_user="$(cat /run/secrets/MINIO_ROOT_USER)"
minio_password="$(cat /run/secrets/MINIO_ROOT_PASSWORD)"
restore_target=/scratch/restore
restore_url="postgresql://postgres:${postgres_password}@restore-postgres:5432/aurum_restore"

minimum_free_bytes="${AURUM_BACKUP_MIN_FREE_BYTES:-5368709120}"
available_bytes="$(df -Pk /scratch | awk 'NR == 2 {print $4 * 1024}')"
[ "$available_bytes" -ge "$minimum_free_bytes" ] || {
    echo "Restore scratch has insufficient free space" >&2
    exit 1
}

rm -rf "$restore_target"
mkdir -p "$HOME" "$TMPDIR" "$restore_target"
restic --no-lock restore latest --tag aurum-combined --target "$restore_target"
manifest_path="$(find "$restore_target" -type f -name manifest.json -print -quit)"
test -n "$manifest_path"
restore_root="$(dirname "$manifest_path")"

test -s "$restore_root/database.dump"
expected_sha256="$(sed -n 's/.*"database_dump_sha256": "\([0-9a-f]*\)".*/\1/p' \
    "$restore_root/manifest.json")"
actual_sha256="$(sha256sum "$restore_root/database.dump" | awk '{print $1}')"
test -n "$expected_sha256"
test "$actual_sha256" = "$expected_sha256"

export PGPASSWORD="$postgres_password"
dropdb --host restore-postgres --username postgres --if-exists aurum_restore
createdb --host restore-postgres --username postgres --owner aurum_schema_owner aurum_restore
pg_restore \
    --dbname "$restore_url" \
    --exit-on-error \
    "$restore_root/database.dump"
pg_amcheck --install-missing "$restore_url"
revision="$(psql --dbname "$restore_url" --tuples-only --no-align \
    --command 'SELECT version_num FROM public.alembic_version')"

mc --config-dir /workspace/mc alias set \
    target http://restore-minio:9000 "$minio_user" "$minio_password" >/dev/null
mc --config-dir /workspace/mc mb --ignore-existing target/aurum-restore >/dev/null
mc --config-dir /workspace/mc version enable target/aurum-restore >/dev/null
if [ -d "$restore_root/minio" ]; then
    mc --config-dir /workspace/mc mirror \
        "$restore_root/minio" target/aurum-restore >/dev/null
fi

expected_objects="$(find "$restore_root/minio" -type f 2>/dev/null | wc -l | tr -d ' ')"
rm -rf /scratch/verified-minio
mkdir -p /scratch/verified-minio
mc --config-dir /workspace/mc mirror target/aurum-restore /scratch/verified-minio >/dev/null
restored_objects="$(find /scratch/verified-minio -type f | wc -l | tr -d ' ')"
test "$restored_objects" = "$expected_objects"
(
    cd /scratch/verified-minio
    find . -type f -exec sha256sum '{}' \; | sort > /scratch/restored-minio-files.sha256
)
cmp "$restore_root/minio-files.sha256" /scratch/restored-minio-files.sha256

expected_rls_tables="$(sed -n 's/.*"database_rls_table_count": \([0-9][0-9]*\).*/\1/p' \
    "$restore_root/manifest.json")"
restored_rls_tables="$(psql --dbname "$restore_url" --tuples-only --no-align \
    --command "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' AND c.relrowsecurity")"
test -n "$expected_rls_tables"
test "$restored_rls_tables" = "$expected_rls_tables"

unsafe_owners="$(psql --dbname "$restore_url" --tuples-only --no-align --command \
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_roles r ON r.oid = c.relowner WHERE c.relkind IN ('r','p','v','m','S') AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' AND r.rolname NOT IN ('aurum_schema_owner','postgres')")"
test "$unsafe_owners" = "0"

probe_relation="$(psql --dbname "$restore_url" --tuples-only --no-align --command \
    "SELECT CASE WHEN to_regclass('public.tenant') IS NOT NULL THEN 'public.tenant' WHEN to_regclass('public.recovery_probe') IS NOT NULL THEN 'public.recovery_probe' END")"
test -n "$probe_relation"
PGPASSWORD="$app_password" psql --host restore-postgres --username aurum_app \
    --dbname aurum_restore --command "SELECT count(*) FROM $probe_relation" >/dev/null
PGPASSWORD="$support_password" psql --host restore-postgres --username aurum_support \
    --dbname aurum_restore --command "SELECT count(*) FROM $probe_relation" >/dev/null
PGPASSWORD="$backup_password" psql --host restore-postgres --username aurum_backup \
    --dbname aurum_restore --command "SELECT count(*) FROM $probe_relation" >/dev/null

printf 'Restore drill passed (objects: %s, revision: %s)\n' \
    "$restored_objects" "$revision"
