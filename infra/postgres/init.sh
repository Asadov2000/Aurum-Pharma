#!/bin/bash
# Initial database bootstrap: creates app and support roles.
# Runs once on first container startup (Postgres official entrypoint).
# Envvars are interpolated by bash heredoc (plain .sql files would not substitute).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE aurum_app WITH LOGIN PASSWORD '${AURUM_APP_PASSWORD}';
    CREATE ROLE aurum_support WITH LOGIN PASSWORD '${AURUM_SUPPORT_PASSWORD}' BYPASSRLS;

    REVOKE ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} FROM PUBLIC;
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO aurum_app;
    GRANT ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} TO aurum_support;
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
        FROM PUBLIC, aurum_app;
    GRANT EXECUTE ON FUNCTION public.similarity_op(TEXT, TEXT)
        TO aurum_app, aurum_support;
    GRANT EXECUTE ON FUNCTION public.gen_random_uuid()
        TO aurum_app, aurum_support;
EOSQL
