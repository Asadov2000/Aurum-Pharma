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
        echo "Required PITR secret is empty: $1" >&2
        exit 1
    }
    printf '%s' "$value"
}

database_url="$(read_secret DATABASE_URL_PITR)"
backup_id="$(date -u +%Y%m%dT%H%M%SZ)-$PPID"
payload=/scratch/pitr-payload
base="$payload/postgres-base"

minimum_free_bytes="${AURUM_BACKUP_MIN_FREE_BYTES:-5368709120}"
available_bytes="$(df -Pk /scratch | awk 'NR == 2 {print $4 * 1024}')"
[ "$available_bytes" -ge "$minimum_free_bytes" ] || {
    echo "PITR scratch has insufficient free space" >&2
    exit 1
}

rm -rf "$payload"
mkdir -p "$HOME" "$TMPDIR" "$payload"

pg_basebackup \
    --dbname "$database_url" \
    --pgdata "$base" \
    --format plain \
    --wal-method stream \
    --checkpoint fast \
    --manifest-checksums SHA256 \
    --no-password
pg_verifybackup "$base"

system_identifier="$(pg_controldata "$base" | sed -n 's/^Database system identifier:[[:space:]]*//p')"
catalog_version="$(pg_controldata "$base" | sed -n 's/^Catalog version number:[[:space:]]*//p')"
manifest_sha256="$(sha256sum "$base/backup_manifest" | awk '{print $1}')"
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

test -n "$system_identifier"
test -n "$catalog_version"
cat > "$payload/pitr-manifest.json" <<EOF
{
  "schema_version": 1,
  "backup_id": "$backup_id",
  "created_at_utc": "$created_at",
  "database_system_identifier": "$system_identifier",
  "catalog_version": "$catalog_version",
  "postgres_backup_manifest_sha256": "$manifest_sha256",
  "wal_method": "stream",
  "verification": "pg_verifybackup"
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
        --tag aurum-pitr-base \
        --tag "$backup_id" \
        .
)
restic check --read-data-subset=5%

printf 'PITR base backup completed: %s (system: %s)\n' \
    "$backup_id" "$system_identifier"
