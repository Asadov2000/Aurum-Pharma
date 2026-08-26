#!/bin/bash
# Initial database bootstrap: creates runtime, migration, and owner roles.
# Runs once on first container startup (Postgres official entrypoint).
# Passwords can come from development environment variables or production
# Docker-secret files. psql reads them with \getenv and quotes them as SQL
# literals, so punctuation in a strong password cannot alter the statement.
set -eu

load_secret() {
    name="$1"
    file_name="${name}_FILE"
    value="$(printenv "$name" 2>/dev/null || true)"
    file_path="$(printenv "$file_name" 2>/dev/null || true)"

    if [ -n "$value" ] && [ -n "$file_path" ]; then
        echo "$name and $file_name cannot both be set" >&2
        exit 1
    fi
    if [ -n "$file_path" ]; then
        [ -f "$file_path" ] || {
            echo "$file_name does not point to a readable file" >&2
            exit 1
        }
        value="$(cat "$file_path")"
    fi
    [ -n "$value" ] || {
        echo "$name is required" >&2
        exit 1
    }
    export "$name=$value"
}

load_secret AURUM_APP_PASSWORD
load_secret AURUM_SUPPORT_PASSWORD
load_secret AURUM_MAILER_PASSWORD
load_secret AURUM_BILLING_WORKER_PASSWORD
load_secret AURUM_MIGRATOR_PASSWORD
load_secret AURUM_BACKUP_PASSWORD
load_secret AURUM_PITR_PASSWORD

role_contract_sql="${AURUM_ROLE_CONTRACT_SQL:-/docker-entrypoint-initdb.d/role-contract.inc}"
[ -r "$role_contract_sql" ] || {
    echo "AURUM_ROLE_CONTRACT_SQL does not point to a readable file" >&2
    exit 1
}
psql \
    -v ON_ERROR_STOP=1 \
    --single-transaction \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --file "$role_contract_sql"

psql \
    -v ON_ERROR_STOP=1 \
    --single-transaction \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" <<-'EOSQL'
    \getenv database_name POSTGRES_DB

    REVOKE ALL PRIVILEGES ON DATABASE :"database_name" FROM PUBLIC;
    GRANT CONNECT ON DATABASE :"database_name"
        TO aurum_app, aurum_mailer, aurum_billing_worker, aurum_backup;
    GRANT ALL PRIVILEGES ON DATABASE :"database_name" TO aurum_support;
    GRANT CONNECT ON DATABASE :"database_name" TO aurum_migrator;
    -- Runtime code may use objects in public, but only the migration/support
    -- role may create or replace them. This keeps SECURITY DEFINER functions
    -- out of reach of the application role.
    ALTER SCHEMA public OWNER TO aurum_support;
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    GRANT USAGE ON SCHEMA public TO aurum_app;
    GRANT ALL ON SCHEMA public TO aurum_support;

    -- DEFAULT PRIVILEGES are scoped to the role that *creates* the object.
    -- Migrations run as aurum_support, so we set defaults FOR THAT ROLE
    -- (otherwise aurum_app gets a "permission denied" on every migration-
    -- created table). The unqualified ALTER DEFAULT PRIVILEGES below also
    -- covers objects created by the postgres superuser.
    -- New objects are deny-by-default for the runtime role. Revoke global
    -- defaults as well: PostgreSQL otherwise grants new functions to PUBLIC,
    -- and schema-scoped rules cannot cancel that built-in global default.
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support
        REVOKE ALL ON TABLES FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support
        REVOKE ALL ON SEQUENCES FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support
        REVOKE ALL ON FUNCTIONS FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner
        REVOKE ALL ON TABLES FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner
        REVOKE ALL ON SEQUENCES FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner
        REVOKE ALL ON FUNCTIONS FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;

    -- Revision 0030 explicitly grants the minimum privileges for current
    -- objects; revision 0031 verifies future objects remain private.
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        REVOKE ALL ON TABLES FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        GRANT ALL ON TABLES TO aurum_support;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        REVOKE ALL ON SEQUENCES FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        GRANT ALL ON SEQUENCES TO aurum_support;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        REVOKE ALL ON FUNCTIONS FROM aurum_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        GRANT ALL ON FUNCTIONS TO aurum_support;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner IN SCHEMA public
        REVOKE ALL ON TABLES FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner IN SCHEMA public
        REVOKE ALL ON SEQUENCES FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner IN SCHEMA public
        REVOKE ALL ON FUNCTIONS FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;

    ALTER DEFAULT PRIVILEGES
        REVOKE ALL ON TABLES FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES
        REVOKE ALL ON SEQUENCES FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES
        REVOKE ALL ON FUNCTIONS FROM PUBLIC, aurum_app;

    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE ALL ON TABLES FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON TABLES TO aurum_support;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE ALL ON SEQUENCES FROM PUBLIC, aurum_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON SEQUENCES TO aurum_support;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        REVOKE ALL ON FUNCTIONS FROM aurum_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON FUNCTIONS TO aurum_support;

    -- Install trusted extensions under the migration role so their lifecycle
    -- stays in Alembic's control. Extension implementation functions are owned
    -- by the database owner, therefore their runtime ACL is set explicitly.
    SET ROLE aurum_support;
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";
    CREATE EXTENSION IF NOT EXISTS "pg_trgm";
    CREATE EXTENSION IF NOT EXISTS "unaccent";
    RESET ROLE;

    REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public
        FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, aurum_billing_worker;
    GRANT EXECUTE ON FUNCTION public.similarity_op(TEXT, TEXT)
        TO aurum_app, aurum_support, aurum_schema_owner;
    GRANT EXECUTE ON FUNCTION public.gen_random_uuid()
        TO aurum_app, aurum_support, aurum_schema_owner;
    GRANT EXECUTE ON FUNCTION public.pgp_sym_encrypt(TEXT, TEXT)
        TO aurum_support, aurum_schema_owner;
    GRANT EXECUTE ON FUNCTION public.pgp_sym_encrypt(TEXT, TEXT, TEXT)
        TO aurum_support, aurum_schema_owner;
    GRANT EXECUTE ON FUNCTION public.pgp_sym_decrypt(BYTEA, TEXT)
        TO aurum_support, aurum_schema_owner;
    GRANT EXECUTE ON FUNCTION public.pgp_sym_decrypt(BYTEA, TEXT, TEXT)
        TO aurum_support, aurum_schema_owner;
EOSQL

unset AURUM_APP_PASSWORD AURUM_SUPPORT_PASSWORD AURUM_MAILER_PASSWORD \
    AURUM_BILLING_WORKER_PASSWORD AURUM_MIGRATOR_PASSWORD AURUM_BACKUP_PASSWORD
