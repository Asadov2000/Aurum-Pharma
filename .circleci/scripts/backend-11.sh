#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
actual_head="$(poetry run alembic heads | awk 'NR == 1 { print $1 }')"
[[ "$actual_head" =~ ^[0-9]{4}$ ]]

run_role_contract() {
  docker exec -i \
    -e POSTGRES_HOST=127.0.0.1 \
    -e POSTGRES_PORT=5432 \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_DB=aurum_test \
    -e POSTGRES_PASSWORD_FILE=/tmp/aurum-postgres-password \
    -e AURUM_APP_PASSWORD_FILE=/tmp/aurum-app-password \
    -e AURUM_SUPPORT_PASSWORD_FILE=/tmp/aurum-support-password \
    -e AURUM_MIGRATOR_PASSWORD_FILE=/tmp/aurum-migrator-password \
    -e AURUM_MAILER_PASSWORD_FILE=/tmp/aurum-mailer-password \
    -e AURUM_BILLING_WORKER_PASSWORD_FILE=/tmp/aurum-billing-worker-password \
    -e AURUM_WORKER_PASSWORD_FILE=/tmp/aurum-worker-password \
    -e AURUM_CATALOG_WORKER_PASSWORD_FILE=/tmp/aurum-catalog-worker-password \
    -e AURUM_BACKUP_PASSWORD_FILE=/tmp/aurum-backup-password \
    -e AURUM_PITR_PASSWORD_FILE=/tmp/aurum-pitr-password \
    "$POSTGRES_SERVICE_ID" sh -c '
      unset \
        POSTGRES_PASSWORD \
        AURUM_APP_PASSWORD \
        AURUM_SUPPORT_PASSWORD \
        AURUM_MIGRATOR_PASSWORD \
        AURUM_MAILER_PASSWORD \
        AURUM_BILLING_WORKER_PASSWORD \
        AURUM_WORKER_PASSWORD \
        AURUM_CATALOG_WORKER_PASSWORD \
        AURUM_BACKUP_PASSWORD \
        AURUM_PITR_PASSWORD
      exec sh /tmp/bootstrap-roles.sh
    '
}

database_security_state() {
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc "
      WITH role_state AS (
        SELECT
          rolname,
          rolcanlogin,
          rolinherit,
          rolsuper,
          rolcreatedb,
          rolcreaterole,
          rolreplication,
          rolbypassrls,
          pg_catalog.md5(COALESCE(rolpassword, '')) AS password_fingerprint
        FROM pg_catalog.pg_authid
        WHERE rolname IN (
          'aurum_app',
          'aurum_edge_cash_executor',
          'aurum_edge_cash_owner',
          'aurum_billing_worker',
          'aurum_mailer',
          'aurum_support',
          'aurum_migrator',
          'aurum_schema_owner'
        )
      ),
      membership_state AS (
        SELECT
          granted.rolname AS granted_role,
          member.rolname AS member_role,
          membership.admin_option,
          membership.inherit_option,
          membership.set_option
        FROM pg_catalog.pg_auth_members AS membership
        JOIN pg_catalog.pg_roles AS granted
          ON granted.oid = membership.roleid
        JOIN pg_catalog.pg_roles AS member
          ON member.oid = membership.member
        WHERE granted.rolname LIKE 'aurum_%'
           OR member.rolname LIKE 'aurum_%'
      ),
      database_state AS (
        SELECT
          pg_catalog.pg_get_userbyid(datdba) AS owner,
          datacl::TEXT AS acl
        FROM pg_catalog.pg_database
        WHERE datname = current_database()
      )
      SELECT pg_catalog.md5(
        COALESCE((
          SELECT pg_catalog.jsonb_agg(
            pg_catalog.to_jsonb(role_state)
            ORDER BY rolname
          )::TEXT
          FROM role_state
        ), '') ||
        COALESCE((
          SELECT pg_catalog.jsonb_agg(
            pg_catalog.to_jsonb(membership_state)
            ORDER BY granted_role, member_role
          )::TEXT
          FROM membership_state
        ), '') ||
        COALESCE((
          SELECT pg_catalog.to_jsonb(database_state)::TEXT
          FROM database_state
        ), '')
      )
    "
}

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "DELETE FROM public.alembic_version"
before_state="$(database_security_state)"
if run_role_contract; then
  echo "bootstrap accepted an empty revision ledger"
  exit 1
fi
test "$before_state" = "$(database_security_state)"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "INSERT INTO public.alembic_version(version_num) VALUES ('0067')"
run_role_contract

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "INSERT INTO public.alembic_version(version_num) VALUES ('0066')"
before_state="$(database_security_state)"
if run_role_contract; then
  echo "bootstrap accepted a multi-head revision ledger"
  exit 1
fi
test "$before_state" = "$(database_security_state)"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "DELETE FROM public.alembic_version WHERE version_num = '0066'"
run_role_contract

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "UPDATE public.alembic_version SET version_num = '9999'"
before_state="$(database_security_state)"
if run_role_contract; then
  echo "bootstrap accepted an unknown revision"
  exit 1
fi
test "$before_state" = "$(database_security_state)"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "UPDATE public.alembic_version SET version_num = '0067'"
run_role_contract

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "ALTER TABLE public.alembic_version RENAME TO alembic_version_missing"
before_state="$(database_security_state)"
if run_role_contract; then
  echo "bootstrap accepted a missing revision ledger in a populated database"
  exit 1
fi
test "$before_state" = "$(database_security_state)"
if poetry run python -m app.migrate upgrade head; then
  echo "migration runner accepted a missing revision ledger in a populated database"
  exit 1
fi
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "ALTER TABLE public.alembic_version_missing RENAME TO alembic_version"
run_role_contract

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "CREATE SCHEMA shadow; CREATE TABLE shadow.alembic_version(version_num TEXT); INSERT INTO shadow.alembic_version VALUES ('9999')"
run_role_contract
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "DROP SCHEMA shadow CASCADE"

# The negative checks intentionally spoof older revisions without
# downgrading objects. Restore the actual head before application tests.
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
    -c "UPDATE public.alembic_version SET version_num = '$actual_head'"
run_role_contract
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT has_schema_privilege('aurum_mailer', 'public', 'USAGE')"
)" = "t"
