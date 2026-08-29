#!/usr/bin/env sh
set -eu

case "$(uname -s)" in
    MINGW*|MSYS*)
        export MSYS_NO_PATHCONV=1
        export MSYS2_ARG_CONV_EXCL='*'
        ;;
esac

project="aurum-recovery-ci-$$"
root="$(mktemp -d "${TMPDIR:-/tmp}/aurum-recovery-ci.XXXXXX")"
secrets="$root/secrets"
repository="$root/repository"
scratch="$root/scratch"
wal_archive="$root/wal-archive"
metrics="$root/metrics"
mkdir -p "$secrets" "$repository" "$scratch" "$wal_archive" "$metrics"

cleanup() {
    status=$?
    set +e
    if [ "$status" -ne 0 ]; then
        docker compose \
            -p "$project" \
            --env-file .env.production.example \
            --file docker-compose.production.yml \
            --file docker-compose.recovery.yml \
            --profile backup \
            --profile restore-drill \
            --profile offsite \
            --profile offsite-test \
            logs --no-color --tail 200 \
            postgres restore-postgres offsite-test-minio >&2
    fi

    docker compose \
        -p "$project" \
        --env-file .env.production.example \
        --file docker-compose.production.yml \
        --file docker-compose.recovery.yml \
        --profile backup \
        --profile restore-drill \
        --profile offsite \
        --profile offsite-test \
        down --volumes --remove-orphans >/dev/null 2>&1 || true

    case "$root" in
        "${TMPDIR:-/tmp}"/aurum-recovery-ci.*) rm -rf -- "$root" ;;
        *) echo "Refusing to remove unexpected recovery test path: $root" >&2 ;;
    esac

    trap - EXIT INT TERM
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

write_secret() {
    printf '%s' "$2" > "$secrets/$1"
    # The parent mktemp directory is 0700. File-backed Compose secrets must
    # remain readable through Docker's user namespace on hosted Linux runners.
    chmod 644 "$secrets/$1"
}

random_hex() {
    od -An -N "$1" -tx1 /dev/urandom | tr -d ' \n'
}

host_bind_path() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        printf '%s' "$1"
    fi
}

postgres_password="$(random_hex 32)"
app_password="$(random_hex 32)"
support_password="$(random_hex 32)"
migrator_password="$(random_hex 32)"
mailer_password="$(random_hex 32)"
billing_password="$(random_hex 32)"
backup_password="$(random_hex 32)"
pitr_password="$(random_hex 32)"
redis_password="$(random_hex 32)"

write_secret POSTGRES_PASSWORD "$postgres_password"
write_secret AURUM_APP_PASSWORD "$app_password"
write_secret AURUM_SUPPORT_PASSWORD "$support_password"
write_secret AURUM_MIGRATOR_PASSWORD "$migrator_password"
write_secret AURUM_MAILER_PASSWORD "$mailer_password"
write_secret AURUM_BILLING_WORKER_PASSWORD "$billing_password"
write_secret AURUM_BACKUP_PASSWORD "$backup_password"
write_secret AURUM_PITR_PASSWORD "$pitr_password"
write_secret DATABASE_URL_APP "postgresql+asyncpg://aurum_app:$app_password@postgres:5432/aurum"
write_secret DATABASE_URL_SUPPORT "postgresql+asyncpg://aurum_support:$support_password@postgres:5432/aurum"
write_secret DATABASE_URL_MIGRATION "postgresql+asyncpg://aurum_migrator:$migrator_password@postgres:5432/aurum"
write_secret DATABASE_URL_MAILER "postgresql+asyncpg://aurum_mailer:$mailer_password@postgres:5432/aurum"
write_secret DATABASE_URL_BILLING_WORKER "postgresql+asyncpg://aurum_billing_worker:$billing_password@postgres:5432/aurum"
write_secret DATABASE_URL_BACKUP "postgresql://aurum_backup:$backup_password@postgres:5432/aurum"
write_secret DATABASE_URL_PITR "postgresql://aurum_pitr:$pitr_password@postgres:5432/aurum"
write_secret REDIS_PASSWORD "$redis_password"
write_secret REDIS_URL "redis://:$redis_password@redis:6379/0"
write_secret JWT_SECRET "$(random_hex 48)"
write_secret MFA_ENCRYPTION_KEY "$(random_hex 48)"
write_secret MFA_ENCRYPTION_PREVIOUS_KEYS '{}'
write_secret EMAIL_OUTBOX_ENCRYPTION_KEY "$(random_hex 48)"
write_secret EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS '{}'
write_secret METRICS_TOKEN "$(random_hex 48)"
write_secret MINIO_ROOT_USER "$(random_hex 10)"
write_secret MINIO_ROOT_PASSWORD "$(random_hex 32)"
write_secret MINIO_ACCESS_KEY "$(random_hex 10)"
write_secret MINIO_SECRET_KEY "$(random_hex 32)"
write_secret MINIO_BACKUP_ACCESS_KEY "$(random_hex 10)"
write_secret MINIO_BACKUP_SECRET_KEY "$(random_hex 32)"
write_secret RESTIC_PASSWORD "$(random_hex 48)"
write_secret AURUM_OFFSITE_ACCESS_KEY "$(random_hex 10)"
write_secret AURUM_OFFSITE_SECRET_KEY "$(random_hex 32)"
write_secret EMAIL_PASSWORD "$(random_hex 32)"

export AURUM_SECRET_FILES_DIR="$(host_bind_path "$secrets")"
export AURUM_BACKUP_REPOSITORY="$(host_bind_path "$repository")"
export AURUM_BACKUP_SCRATCH="$(host_bind_path "$scratch")"
export AURUM_WAL_ARCHIVE="$(host_bind_path "$wal_archive")"
export AURUM_RECOVERY_METRICS_DIR="$metrics"
export AURUM_BACKUP_MIN_FREE_BYTES=1048576
export AURUM_IMAGE_TAG=ci
export AURUM_OFFSITE_SECRET_FILES_DIR="$(host_bind_path "$secrets")"
export AURUM_OFFSITE_ENDPOINT=http://offsite-test-minio:9000
export AURUM_OFFSITE_BUCKET=aurum-offsite-test
export AURUM_OFFSITE_PREFIX=aurum-ci
export AURUM_OFFSITE_ALLOW_INSECURE=true
export AURUM_PITR_TARGET_NAME=aurum_ci_target

# The local bind directory is writable only by the non-root backup UID.
chmod 700 "$repository" "$scratch"
chmod 750 "$wal_archive"
case "$(uname -s)" in
    MINGW*|MSYS*) ;;
    *)
        sudo chown 10001:10001 "$repository" "$scratch"
        sudo chown 70:70 "$wal_archive"
        ;;
esac

for recovery_script in \
    scripts/recovery-metrics.sh \
    scripts/run-production-recovery-cycle.sh \
    scripts/run-production-restore-drill.sh; do
    sh -n "$recovery_script"
done

. ./scripts/recovery-metrics.sh
aurum_run_recovery_step metrics_probe sh -c 'exit 0'
metrics_file="$metrics/aurum-recovery-metrics_probe.prom"
grep -q 'aurum_recovery_job_last_status{component="metrics_probe"} 1' \
    "$metrics_file"
last_success_before="$(sed -n \
    's/^aurum_recovery_job_last_success_timestamp_seconds{component="metrics_probe"} \([0-9][0-9]*\)$/\1/p' \
    "$metrics_file")"
test -n "$last_success_before"
if aurum_run_recovery_step metrics_probe sh -c 'exit 7'; then
    echo "Failure metrics probe unexpectedly succeeded" >&2
    exit 1
fi
grep -q 'aurum_recovery_job_last_status{component="metrics_probe"} 0' \
    "$metrics_file"
grep -q \
    "aurum_recovery_job_last_success_timestamp_seconds{component=\"metrics_probe\"} $last_success_before" \
    "$metrics_file"
test -z "$(find "$metrics" -type f ! -name '*.prom' -print -quit)"

grep -q 'OnUnitActiveSec=5m' infra/systemd/aurum-wal-offsite.timer
grep -q 'OnCalendar=\*-\*-01 03:30:00' infra/systemd/aurum-restore-drill.timer
grep -q 'run-production-restore-drill.sh' infra/systemd/aurum-restore-drill.service
grep -q 'TimeoutStartSec=4h' infra/systemd/aurum-backup.service
grep -q 'TimeoutStartSec=30m' infra/systemd/aurum-wal-offsite.service
grep -q 'TimeoutStartSec=4h' infra/systemd/aurum-restore-drill.service
export AURUM_RECOVERY_METRICS_DIR="$(host_bind_path "$metrics")"

compose() {
    docker compose \
        -p "$project" \
        --env-file .env.production.example \
        --file docker-compose.production.yml \
        --file docker-compose.recovery.yml \
        "$@"
}

compose up -d --wait postgres minio

docker exec -i "${project}-postgres-1" psql \
    -v ON_ERROR_STOP=1 \
    -U postgres \
    -d aurum <<'SQL'
CREATE TABLE public.alembic_version (version_num VARCHAR(32) PRIMARY KEY);
INSERT INTO public.alembic_version (version_num) VALUES ('ci-recovery');
CREATE TABLE public.recovery_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO public.recovery_probe (id, value) VALUES (1, 'verified');
CREATE TABLE public.pitr_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO public.pitr_probe (id, value) VALUES (1, 'before_base');
ALTER TABLE public.recovery_probe ENABLE ROW LEVEL SECURITY;
CREATE POLICY recovery_probe_read ON public.recovery_probe FOR SELECT USING (true);
GRANT SELECT ON public.recovery_probe TO aurum_app, aurum_support;
ALTER TABLE public.recovery_probe OWNER TO aurum_schema_owner;
SQL

compose run --rm minio-init
compose run --rm --entrypoint /bin/sh minio-init -ec '
    root_user="$(cat /run/secrets/MINIO_ROOT_USER)"
    root_password="$(cat /run/secrets/MINIO_ROOT_PASSWORD)"
    mc --config-dir /tmp/.mc alias set local http://minio:9000 "$root_user" "$root_password" >/dev/null
    printf verified | mc --config-dir /tmp/.mc pipe local/aurum/recovery/probe.txt >/dev/null
'

compose --profile backup run --rm backup
compose --profile backup run --rm pitr-basebackup

expected_wal="$(docker exec -i "${project}-postgres-1" psql \
    -U postgres \
    -d aurum \
    --tuples-only \
    --no-align <<'SQL'
INSERT INTO public.pitr_probe (id, value) VALUES (2, 'must_survive');
SELECT pg_create_restore_point('aurum_ci_target');
INSERT INTO public.pitr_probe (id, value) VALUES (3, 'must_not_survive');
SELECT pg_walfile_name(pg_switch_wal());
SQL
)"
expected_wal="$(printf '%s\n' "$expected_wal" | tail -n 1 | tr -d '[:space:]')"
test -n "$expected_wal"

attempt=1
until docker exec "${project}-postgres-1" test -f "/wal-archive/$expected_wal.gz"; do
    if [ "$attempt" -ge 30 ]; then
        echo "Expected WAL segment was not archived: $expected_wal" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 1
done

compose --profile backup run --rm wal-snapshot
compose --profile restore-drill run --rm restore-drill
compose --profile restore-drill run --rm pitr-restore-drill

compose --profile offsite-test up -d --wait offsite-test-minio
compose --profile offsite-test run --rm offsite-test-init
compose --profile offsite run --rm offsite-sync

compose --profile offsite-test run --rm --entrypoint /bin/sh offsite-test-init -ec '
    root_user="$(cat /run/secrets/MINIO_ROOT_USER)"
    root_password="$(cat /run/secrets/MINIO_ROOT_PASSWORD)"
    append_user="$(cat /run/secrets/AURUM_OFFSITE_ACCESS_KEY)"
    append_password="$(cat /run/secrets/AURUM_OFFSITE_SECRET_KEY)"
    mc --config-dir /tmp/root alias set root http://offsite-test-minio:9000 "$root_user" "$root_password" >/dev/null
    mc --config-dir /tmp/append alias set append http://offsite-test-minio:9000 "$append_user" "$append_password" >/dev/null
    mc --config-dir /tmp/root find \
        root/aurum-offsite-test/aurum-ci/repository \
        --name '*' \
        --versions \
        --print '{version} {}' > /tmp/offsite-objects
    version=
    object=
    while IFS=' ' read -r candidate_version candidate; do
        version="$candidate_version"
        object="$candidate"
        break
    done < /tmp/offsite-objects
    test -n "$version"
    test -n "$object"
    append_object="append/${object#root/}"
    if mc --config-dir /tmp/append rm --version-id "$version" "$append_object" >/dev/null 2>&1; then
        echo "Append-only off-site credential unexpectedly deleted an object" >&2
        exit 1
    fi
    if mc --config-dir /tmp/root rm --version-id "$version" "$object" >/dev/null 2>&1; then
        echo "COMPLIANCE Object Lock unexpectedly allowed version deletion" >&2
        exit 1
    fi
    mc --config-dir /tmp/root stat "$object" >/dev/null
'
compose --profile offsite-test run --rm offsite-test-restore

echo "Recovery tooling integration test passed"
