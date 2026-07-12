#!/bin/bash
# Initial database bootstrap: creates app and support roles.
# Runs once on first container startup (Postgres official entrypoint).
# Envvars are interpolated by bash heredoc (plain .sql files would not substitute).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE aurum_app WITH LOGIN PASSWORD '${AURUM_APP_PASSWORD}';
    CREATE ROLE aurum_support WITH LOGIN PASSWORD '${AURUM_SUPPORT_PASSWORD}' BYPASSRLS;

    GRANT ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} TO aurum_app, aurum_support;
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
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        GRANT ALL ON TABLES TO aurum_app, aurum_support;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        GRANT ALL ON SEQUENCES TO aurum_app, aurum_support;
    ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public
        GRANT ALL ON FUNCTIONS TO aurum_app, aurum_support;

    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON TABLES TO aurum_app, aurum_support;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON SEQUENCES TO aurum_app, aurum_support;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON FUNCTIONS TO aurum_app, aurum_support;
EOSQL
