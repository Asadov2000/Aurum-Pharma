#!/bin/sh
set -eu

umask 077
export HOME=/workspace/home
export TMPDIR=/workspace/tmp
export RESTIC_REPOSITORY=/repository
export RESTIC_PASSWORD_FILE=/run/secrets/RESTIC_PASSWORD

read_secret() {
    value="$(cat "/run/secrets/$1")"
    [ -n "$value" ] || {
        echo "Required backup secret is empty: $1" >&2
        exit 1
    }
    printf '%s' "$value"
}

database_url="$(read_secret DATABASE_URL_BACKUP)"
minio_user="$(read_secret MINIO_BACKUP_ACCESS_KEY)"
minio_password="$(read_secret MINIO_BACKUP_SECRET_KEY)"
backup_id="$(date -u +%Y%m%dT%H%M%SZ)-$PPID"
payload=/scratch/payload

minimum_free_bytes="${AURUM_BACKUP_MIN_FREE_BYTES:-5368709120}"
available_bytes="$(df -Pk /scratch | awk 'NR == 2 {print $4 * 1024}')"
[ "$available_bytes" -ge "$minimum_free_bytes" ] || {
    echo "Backup scratch has insufficient free space" >&2
    exit 1
}

rm -rf "$payload"
mkdir -p "$HOME" "$TMPDIR" "$payload/minio"

pg_dump \
    --dbname "$database_url" \
    --format custom \
    --compress 9 \
    --file "$payload/database.dump"

wal_lsn="$(psql --dbname "$database_url" --tuples-only --no-align \
    --command 'SELECT pg_current_wal_lsn()')"
alembic_revision="$(psql --dbname "$database_url" --tuples-only --no-align \
    --command 'SELECT version_num FROM public.alembic_version')"

mc --config-dir /workspace/mc alias set \
    source https://minio:9000 "$minio_user" "$minio_password" >/dev/null
mc --config-dir /workspace/mc mirror source/aurum "$payload/minio" >/dev/null

dump_sha256="$(sha256sum "$payload/database.dump" | awk '{print $1}')"
object_count="$(find "$payload/minio" -type f | wc -l | tr -d ' ')"
(
    cd "$payload/minio"
    find . -type f -exec sha256sum '{}' \; | sort > "$payload/minio-files.sha256"
)
rls_table_count="$(psql --dbname "$database_url" --tuples-only --no-align \
    --command "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' AND c.relrowsecurity")"
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "$payload/manifest.json" <<EOF
{
  "schema_version": 1,
  "backup_id": "$backup_id",
  "created_at_utc": "$created_at",
  "database_wal_lsn": "$wal_lsn",
  "alembic_revision": "$alembic_revision",
  "database_dump_sha256": "$dump_sha256",
  "database_rls_table_count": $rls_table_count,
  "minio_object_count": $object_count,
  "minio_hash_manifest": "minio-files.sha256",
  "consistency_mode": "postgres-consistent-dump-plus-current-object-snapshot"
}
EOF

if [ ! -f /repository/config ]; then
    restic init
fi
restic snapshots >/dev/null
(
    cd "$payload"
    restic backup \
        --host aurum-production \
        --tag aurum-combined \
        --tag "$backup_id" \
        .
)
restic forget \
    --tag aurum-combined \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 12 \
    --prune
restic check --read-data-subset=5%

printf 'Backup completed: %s (objects: %s, revision: %s)\n' \
    "$backup_id" "$object_count" "$alembic_revision"
