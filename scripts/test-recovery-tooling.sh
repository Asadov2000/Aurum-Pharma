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
mkdir -p "$secrets" "$repository" "$scratch"

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
            logs --no-color --tail 200 postgres restore-postgres >&2
    fi

    docker compose \
        -p "$project" \
        --env-file .env.production.example \
        --file docker-compose.production.yml \
        --file docker-compose.recovery.yml \
        --profile backup \
        --profile restore-drill \
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
redis_password="$(random_hex 32)"

write_secret POSTGRES_PASSWORD "$postgres_password"
write_secret AURUM_APP_PASSWORD "$app_password"
write_secret AURUM_SUPPORT_PASSWORD "$support_password"
write_secret AURUM_MIGRATOR_PASSWORD "$migrator_password"
write_secret AURUM_MAILER_PASSWORD "$mailer_password"
write_secret AURUM_BILLING_WORKER_PASSWORD "$billing_password"
write_secret AURUM_BACKUP_PASSWORD "$backup_password"
write_secret DATABASE_URL_APP "postgresql+asyncpg://aurum_app:$app_password@postgres:5432/aurum"
write_secret DATABASE_URL_SUPPORT "postgresql+asyncpg://aurum_support:$support_password@postgres:5432/aurum"
write_secret DATABASE_URL_MIGRATION "postgresql+asyncpg://aurum_migrator:$migrator_password@postgres:5432/aurum"
write_secret DATABASE_URL_MAILER "postgresql+asyncpg://aurum_mailer:$mailer_password@postgres:5432/aurum"
write_secret DATABASE_URL_BILLING_WORKER "postgresql+asyncpg://aurum_billing_worker:$billing_password@postgres:5432/aurum"
write_secret DATABASE_URL_BACKUP "postgresql://aurum_backup:$backup_password@postgres:5432/aurum"
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
write_secret EMAIL_PASSWORD "$(random_hex 32)"

export AURUM_SECRET_FILES_DIR="$(host_bind_path "$secrets")"
export AURUM_BACKUP_REPOSITORY="$(host_bind_path "$repository")"
export AURUM_BACKUP_SCRATCH="$(host_bind_path "$scratch")"
export AURUM_BACKUP_MIN_FREE_BYTES=1048576
export AURUM_IMAGE_TAG=ci

# The local bind directory is writable only by the non-root backup UID.
chmod 700 "$repository" "$scratch"
case "$(uname -s)" in
    MINGW*|MSYS*) ;;
    *) sudo chown 10001:10001 "$repository" "$scratch" ;;
esac

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
compose --profile restore-drill run --rm restore-drill

echo "Recovery tooling integration test passed"
