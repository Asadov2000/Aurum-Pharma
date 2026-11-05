#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
actual_head="$(poetry run alembic heads | awk 'NR == 1 { print $1 }')"
actual_head_number="$(printf '%s' "$actual_head" | sed 's/^0*//')"
role_contract_head="$(
  sed -nE \
    's/.*revision_number > ([0-9]+).*/\1/p' \
    ../infra/postgres/role-contract.sql
)"
test -n "$actual_head"
[[ "$actual_head" =~ ^[0-9]{4}$ ]]
test -n "$actual_head_number"
test "$actual_head_number" = "$role_contract_head"

run_role_bootstrap() {
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

docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
GRANT aurum_support TO aurum_edge_cash_executor;
CREATE SCHEMA edge_acl_probe;
CREATE TABLE edge_acl_probe.probe (id INTEGER);
CREATE PROCEDURE edge_acl_probe.probe_procedure()
LANGUAGE SQL AS $$ SELECT 1 $$;
GRANT USAGE ON SCHEMA edge_acl_probe TO aurum_edge_cash_executor;
GRANT SELECT ON TABLE edge_acl_probe.probe TO aurum_edge_cash_executor;
GRANT EXECUTE ON PROCEDURE edge_acl_probe.probe_procedure()
  TO aurum_edge_cash_executor;
SQL
run_role_bootstrap
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT pg_has_role('aurum_edge_cash_executor', 'aurum_support', 'MEMBER')"
)" = "f"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT has_schema_privilege('aurum_edge_cash_executor', 'edge_acl_probe', 'USAGE')"
)" = "f"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT has_table_privilege('aurum_edge_cash_executor', 'edge_acl_probe.probe', 'SELECT')"
)" = "f"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT has_function_privilege('aurum_edge_cash_executor', routines.oid, 'EXECUTE') FROM pg_proc AS routines JOIN pg_namespace AS schemas ON schemas.oid = routines.pronamespace WHERE schemas.nspname = 'edge_acl_probe' AND routines.proname = 'probe_procedure'"
)" = "f"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT bool_and(rolpassword IS NULL) FROM pg_authid WHERE rolname IN ('aurum_edge_cash_executor', 'aurum_edge_cash_owner')"
)" = "t"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "DROP SCHEMA edge_acl_probe CASCADE"

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "CREATE SCHEMA edge_owner_probe AUTHORIZATION aurum_edge_cash_owner"
if run_role_bootstrap; then
  echo "role bootstrap accepted an Edge-owned sentinel schema"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'edge_owner_probe'"
)" = "aurum_edge_cash_owner"
docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
ALTER SCHEMA edge_owner_probe OWNER TO postgres;
DROP SCHEMA edge_owner_probe;
SQL

docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
CREATE TYPE public.edge_owner_probe AS ENUM ('probe');
ALTER TYPE public.edge_owner_probe OWNER TO aurum_edge_cash_owner;
SQL
if run_role_bootstrap; then
  echo "role bootstrap accepted an Edge-owned sentinel type"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT pg_get_userbyid(types.typowner) FROM pg_type AS types JOIN pg_namespace AS schemas ON schemas.oid = types.typnamespace WHERE schemas.nspname = 'public' AND types.typname = 'edge_owner_probe'"
)" = "aurum_edge_cash_owner"
docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
ALTER TYPE public.edge_owner_probe OWNER TO postgres;
DROP TYPE public.edge_owner_probe;
SQL

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO aurum_edge_cash_executor"
if run_role_bootstrap; then
  echo "role bootstrap accepted an Edge default privilege"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT count(*) FROM pg_default_acl AS defaults CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl JOIN pg_roles AS grantees ON grantees.oid = acl.grantee WHERE grantees.rolname = 'aurum_edge_cash_executor'"
)" = "1"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE SELECT ON TABLES FROM aurum_edge_cash_executor"

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "CREATE DATABASE aurum_edge_sentinel_test OWNER aurum_edge_cash_owner"
if run_role_bootstrap; then
  echo "role bootstrap accepted an Edge-owned sentinel database"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d postgres -Atc \
    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'aurum_edge_sentinel_test'"
)" = "aurum_edge_cash_owner"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "DROP DATABASE aurum_edge_sentinel_test"

docker exec -u postgres "$POSTGRES_SERVICE_ID" \
  mkdir -p /tmp/aurum-edge-tablespace
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "CREATE TABLESPACE aurum_edge_sentinel OWNER aurum_edge_cash_owner LOCATION '/tmp/aurum-edge-tablespace'"
if run_role_bootstrap; then
  echo "role bootstrap accepted an Edge-owned sentinel tablespace"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d postgres -Atc \
    "SELECT pg_get_userbyid(spcowner) FROM pg_tablespace WHERE spcname = 'aurum_edge_sentinel'"
)" = "aurum_edge_cash_owner"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "DROP TABLESPACE aurum_edge_sentinel"

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "CREATE DATABASE aurum_sentinel_test OWNER aurum_support"
if poetry run python -m app.migrate upgrade head; then
  echo "0067 upgrade ignored a support-owned sentinel database"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d postgres -Atc \
    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'aurum_sentinel_test'"
)" = "aurum_support"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "DROP DATABASE aurum_sentinel_test"

docker exec -u postgres "$POSTGRES_SERVICE_ID" \
  mkdir -p /tmp/aurum-support-tablespace
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "CREATE TABLESPACE aurum_support_sentinel OWNER aurum_support LOCATION '/tmp/aurum-support-tablespace'"
if poetry run python -m app.migrate upgrade head; then
  echo "0067 upgrade ignored a support-owned sentinel tablespace"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d postgres -Atc \
    "SELECT pg_get_userbyid(spcowner) FROM pg_tablespace WHERE spcname = 'aurum_support_sentinel'"
)" = "aurum_support"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "DROP TABLESPACE aurum_support_sentinel"

poetry run python -m app.migrate upgrade 0091
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "REVOKE REFERENCES ON TABLE public.app_user, public.tenant FROM aurum_schema_owner"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT has_table_privilege('aurum_schema_owner', 'public.app_user', 'REFERENCES')"
)" = "f"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT has_table_privilege('aurum_schema_owner', 'public.tenant', 'REFERENCES')"
)" = "f"
poetry run python -m app.migrate upgrade head
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT has_table_privilege('aurum_schema_owner', 'public.app_user', 'REFERENCES')"
)" = "f"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT has_table_privilege('aurum_schema_owner', 'public.tenant', 'REFERENCES')"
)" = "f"
# Restore the fixture before the independent downgrade/upgrade roundtrip.
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test \
  -c "GRANT REFERENCES ON TABLE public.app_user, public.tenant TO aurum_schema_owner"

docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
-- Historical subscription fixtures use the canonical maintenance identity.
SET SESSION AUTHORIZATION aurum_migrator;
SET ROLE aurum_schema_owner;
INSERT INTO public.app_user (id, email, full_name, status)
VALUES (
  '94000000-0000-4000-8000-000000000001',
  'billing-0094-downgrade@example.invalid',
  'Billing 0094 downgrade guard',
  'active'
);
INSERT INTO public.billing_plan (code, name, created_by)
VALUES (
  'billing_0094_guard',
  'Billing 0094 downgrade guard',
  '94000000-0000-4000-8000-000000000001'
);
INSERT INTO public.tenant (id, name, contact_email)
VALUES (
  '94000000-0000-4000-8000-000000000002',
  'Billing 0094 legacy tenant',
  'billing-0094-legacy@example.invalid'
);
INSERT INTO public.tenant_subscription (
  id, tenant_id, plan_id, status, period_end, branches_count, amount
) VALUES (
  '94000000-0000-4000-8000-000000000003',
  '94000000-0000-4000-8000-000000000002',
  (SELECT id FROM public.subscription_plan WHERE code = 'aurum_pharma'),
  'active',
  now() + interval '30 days',
  1,
  321.00
);
SQL
if poetry run python -m app.migrate downgrade 0093 --allow-test-downgrade; then
  echo "0094 downgrade removed non-empty versioned pricing tables"
  exit 1
fi
docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
-- The rejected downgrade rolled back, so retirement guards remain active.
SET SESSION AUTHORIZATION aurum_migrator;
SET ROLE aurum_schema_owner;
DELETE FROM public.billing_plan WHERE code = 'billing_0094_guard';
DELETE FROM public.app_user
WHERE id = '94000000-0000-4000-8000-000000000001';
SQL
poetry run python -m app.migrate downgrade 0093 --allow-test-downgrade
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT version_num FROM public.alembic_version"
)" = "0093"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT to_regclass('public.billing_plan') IS NULL"
)" = "t"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT count(*) = 1 AND bool_and(amount = 321.00) FROM public.tenant_subscription WHERE id = '94000000-0000-4000-8000-000000000003'"
)" = "t"
poetry run python -m app.migrate upgrade head
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT count(*) = 1 AND bool_and(amount = 321.00) FROM public.tenant_subscription WHERE id = '94000000-0000-4000-8000-000000000003'"
)" = "t"
# 0095 is independently reversible only while its immutable
# administration journal is empty. Existing 0094 pricing data stays.
poetry run python -m app.migrate downgrade 0094 --allow-test-downgrade
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT version_num FROM public.alembic_version"
)" = "0094"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT to_regclass('public.billing_plan') IS NOT NULL"
)" = "t"
poetry run python -m app.migrate upgrade 0095
docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
INSERT INTO public.tenant_subscription (
  id, tenant_id, plan_id, status, period_start, period_end,
  branches_count, amount
) VALUES (
  '94000000-0000-4000-8000-000000000004',
  '94000000-0000-4000-8000-000000000002',
  (SELECT id FROM public.subscription_plan WHERE code = 'aurum_pharma'),
  'active',
  now() + interval '30 days',
  now() + interval '60 days',
  1,
  654.00
);
SQL
if poetry run python -m app.migrate upgrade head; then
  echo "0096 accepted ambiguous current tenant subscriptions"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT version_num FROM public.alembic_version"
)" = "0095"
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d aurum_test -Atc \
    "SELECT string_agg(id::text || ':' || status, ',' ORDER BY id) FROM public.tenant_subscription WHERE tenant_id = '94000000-0000-4000-8000-000000000002'"
)" = "94000000-0000-4000-8000-000000000003:active,94000000-0000-4000-8000-000000000004:active"
docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
DELETE FROM public.tenant_subscription
WHERE id = '94000000-0000-4000-8000-000000000004';
SQL
poetry run python -m app.migrate upgrade head
docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
-- Clean historical fixtures through maintenance after restoring head.
SET SESSION AUTHORIZATION aurum_migrator;
SET ROLE aurum_schema_owner;
DELETE FROM public.tenant_subscription
WHERE id = '94000000-0000-4000-8000-000000000003';
DELETE FROM public.tenant_settings
WHERE tenant_id = '94000000-0000-4000-8000-000000000002';
DELETE FROM public.tenant
WHERE id = '94000000-0000-4000-8000-000000000002';
SQL

docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "CREATE DATABASE aurum_sentinel_test OWNER aurum_schema_owner"
if poetry run python -m app.migrate downgrade 0066 --allow-test-downgrade; then
  echo "0067 downgrade ignored an owner-owned sentinel database"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d postgres -Atc \
    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'aurum_sentinel_test'"
)" = "aurum_schema_owner"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "DROP DATABASE aurum_sentinel_test"

docker exec -u postgres "$POSTGRES_SERVICE_ID" \
  mkdir -p /tmp/aurum-owner-tablespace
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "CREATE TABLESPACE aurum_owner_sentinel OWNER aurum_schema_owner LOCATION '/tmp/aurum-owner-tablespace'"
if poetry run python -m app.migrate downgrade 0066 --allow-test-downgrade; then
  echo "0067 downgrade ignored an owner-owned sentinel tablespace"
  exit 1
fi
test "$(
  docker exec "$POSTGRES_SERVICE_ID" \
    psql -U postgres -d postgres -Atc \
    "SELECT pg_get_userbyid(spcowner) FROM pg_tablespace WHERE spcname = 'aurum_owner_sentinel'"
)" = "aurum_schema_owner"
docker exec "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "DROP TABLESPACE aurum_owner_sentinel"

poetry run python -m app.migrate downgrade 0055 --allow-test-downgrade
run_role_bootstrap
poetry run python -m app.migrate upgrade head
run_role_bootstrap

# A repeated bootstrap must remove accidental table AND column grants.
# The following pytest exact-ACL checks run through the restricted login.
docker exec -i "$POSTGRES_SERVICE_ID" \
  psql -v ON_ERROR_STOP=1 -U postgres -d aurum_test <<'SQL'
GRANT SELECT ON public.app_user TO aurum_catalog_worker;
GRANT UPDATE (image_version) ON public.tenant_catalog TO aurum_catalog_worker;
GRANT aurum_app TO aurum_catalog_worker;
ALTER ROLE aurum_catalog_worker CONNECTION LIMIT 5;
SQL
run_role_bootstrap
