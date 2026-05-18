#!/bin/bash
# Initial database bootstrap: creates app and support roles.
# Runs once on first container startup (Postgres official entrypoint).
# Envvars are interpolated by bash heredoc (plain .sql files would not substitute).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE aurum_app WITH LOGIN PASSWORD '${AURUM_APP_PASSWORD}';
    CREATE ROLE aurum_support WITH LOGIN PASSWORD '${AURUM_SUPPORT_PASSWORD}' BYPASSRLS;

    GRANT ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} TO aurum_app, aurum_support;
    GRANT ALL ON SCHEMA public TO aurum_app, aurum_support;

    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON TABLES TO aurum_app, aurum_support;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON SEQUENCES TO aurum_app, aurum_support;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT ALL ON FUNCTIONS TO aurum_app, aurum_support;
EOSQL
