#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
test -n "$POSTGRES_SERVICE_ID"
docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
CREATE ROLE aurum_app
  LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS PASSWORD 'aurum_app_pw';
CREATE ROLE aurum_support
  LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION BYPASSRLS PASSWORD 'aurum_support_pw';
ALTER SCHEMA public OWNER TO aurum_support;
GRANT ALL PRIVILEGES ON DATABASE aurum_test TO aurum_support;
GRANT CONNECT ON DATABASE aurum_test TO aurum_app;
SQL
poetry run python -m app.migrate legacy-upgrade 0029
POSTGRES_USER=postgres \
POSTGRES_PASSWORD=postgres \
MIGRATION_DB_HOST=localhost \
MIGRATION_DB_NAME=aurum_test \
  poetry run python -m app.db_owner_migrate
poetry run python -m app.migrate legacy-upgrade 0066
