#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD_FILE:?POSTGRES_PASSWORD_FILE is required}"

[ "${CI:-}" = "true" ] \
    && [ "${GITHUB_ACTIONS:-}" = "true" ] \
    && [ "${AURUM_DISPOSABLE_DB_CONFIRM:-}" = "destroy-this-database" ] || {
    echo "Edge dispatcher tests require an explicitly disposable CI database" >&2
    exit 1
}

case "$POSTGRES_DB" in
    *_test) ;;
    *)
        echo "Edge dispatcher tests require a disposable *_test database" >&2
        exit 1
        ;;
esac

export PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-5}"
export PGOPTIONS="${PGOPTIONS:--c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=15000}"
postgres_host="${POSTGRES_HOST:-127.0.0.1}"
postgres_port="${POSTGRES_PORT:-5432}"

psql_super() {
    psql -v ON_ERROR_STOP=1 -qAt \
        --host "$postgres_host" \
        --port "$postgres_port" \
        --username "$POSTGRES_USER" \
        --dbname "$POSTGRES_DB" \
        "$@"
}

edge_role=""
edge_password=""
tenant_id=""
cashier_id=""

cleanup() {
    original_status=$?
    cleanup_status=0
    trap - EXIT HUP INT TERM

    if [ -z "$edge_role" ]; then
        exit "$original_status"
    fi

    if ! psql_super \
        -v edge_role="$edge_role" \
        -v tenant_id="$tenant_id" \
        -v cashier_id="$cashier_id" <<'SQL'
BEGIN;
DO $cleanup$
DECLARE
  relation REGCLASS;
BEGIN
  FOR relation IN
    SELECT relations.oid::REGCLASS
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = relations.relnamespace
    WHERE schemas.nspname = 'public'
      AND relations.relkind IN ('r', 'p')
  LOOP
    EXECUTE pg_catalog.format(
      'ALTER TABLE %s DISABLE TRIGGER USER',
      relation
    );
  END LOOP;
END
$cleanup$;
DELETE FROM public.audit_log WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.tenant WHERE id = :'tenant_id'::UUID;
DELETE FROM public.app_user WHERE id = :'cashier_id'::UUID;
SET CONSTRAINTS ALL IMMEDIATE;
DO $cleanup$
DECLARE
  relation REGCLASS;
BEGIN
  FOR relation IN
    SELECT relations.oid::REGCLASS
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = relations.relnamespace
    WHERE schemas.nspname = 'public'
      AND relations.relkind IN ('r', 'p')
  LOOP
    EXECUTE pg_catalog.format(
      'ALTER TABLE %s ENABLE TRIGGER USER',
      relation
    );
  END LOOP;
END
$cleanup$;
COMMIT;
REVOKE aurum_edge_cash_executor FROM :"edge_role";
REVOKE CONNECT ON DATABASE :"DBNAME" FROM :"edge_role";
ALTER ROLE :"edge_role" NOLOGIN PASSWORD NULL;
DROP OWNED BY :"edge_role";
DROP ROLE :"edge_role";
SQL
    then
        echo "Failed to clean up Edge dispatcher test identity" >&2
        cleanup_status=1
    fi

    if [ "$original_status" -ne 0 ]; then
        exit "$original_status"
    fi
    exit "$cleanup_status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

scaffold="$(psql_super <<'SQL'
BEGIN;
CREATE TEMP TABLE edge_dispatcher_context ON COMMIT DROP AS
SELECT
  pg_catalog.gen_random_uuid() AS tenant_id,
  pg_catalog.gen_random_uuid() AS cashier_id,
  pg_catalog.gen_random_uuid() AS membership_id,
  pg_catalog.gen_random_uuid() AS role_id,
  pg_catalog.gen_random_uuid() AS branch_id,
  pg_catalog.gen_random_uuid() AS register_id,
  pg_catalog.gen_random_uuid() AS shift_id,
  pg_catalog.gen_random_uuid() AS catalog_id,
  pg_catalog.gen_random_uuid() AS batch_id,
  pg_catalog.gen_random_uuid() AS cloud_node_id,
  pg_catalog.gen_random_uuid() AS edge_node_id,
  pg_catalog.gen_random_uuid() AS activation_id,
  pg_catalog.encode(public.gen_random_bytes(32), 'hex') AS role_password,
  pg_catalog.repeat('0', 64) AS zero_hash;

ALTER TABLE public.tenant DISABLE TRIGGER USER;
ALTER TABLE public.tenant_settings DISABLE TRIGGER USER;
ALTER TABLE public.app_user DISABLE TRIGGER USER;
ALTER TABLE public.tenant_membership DISABLE TRIGGER USER;
ALTER TABLE public.role DISABLE TRIGGER USER;
ALTER TABLE public.role_permission DISABLE TRIGGER USER;
ALTER TABLE public.user_assignment DISABLE TRIGGER USER;
ALTER TABLE public.branch DISABLE TRIGGER USER;
ALTER TABLE public.register DISABLE TRIGGER USER;
ALTER TABLE public.shift DISABLE TRIGGER USER;
ALTER TABLE public.tenant_catalog DISABLE TRIGGER USER;
ALTER TABLE public.batch DISABLE TRIGGER USER;
ALTER TABLE public.sync_node DISABLE TRIGGER USER;
ALTER TABLE public.sync_writer_activation DISABLE TRIGGER USER;
ALTER TABLE public.sync_writer_epoch DISABLE TRIGGER USER;
ALTER TABLE public.sync_stream DISABLE TRIGGER USER;

INSERT INTO public.tenant (id, name, contact_email, status)
SELECT tenant_id, 'Edge dispatcher test',
       'edge-dispatcher-' || tenant_id::TEXT || '@aurum.test', 'active'
FROM edge_dispatcher_context;
INSERT INTO public.tenant_settings (tenant_id, pos_payment_methods)
SELECT tenant_id, '["cash"]'::JSONB FROM edge_dispatcher_context;
INSERT INTO public.app_user (id, email, full_name, home_tenant_id, status)
SELECT cashier_id,
       'edge-cashier-' || cashier_id::TEXT || '@aurum.test',
       'Edge cashier', tenant_id, 'active'
FROM edge_dispatcher_context;
INSERT INTO public.tenant_membership (
  id, tenant_id, user_id, full_name, status, activated_at
)
SELECT membership_id, tenant_id, cashier_id, 'Edge cashier', 'active', now()
FROM edge_dispatcher_context;
INSERT INTO public.role (id, tenant_id, name, level, is_active)
SELECT role_id, tenant_id, 'Edge cashier role', 4, true
FROM edge_dispatcher_context;
INSERT INTO public.role_permission (role_id, permission_code)
SELECT role_id, 'pos.sell' FROM edge_dispatcher_context;
INSERT INTO public.branch (id, tenant_id, name, is_active)
SELECT branch_id, tenant_id, 'Edge branch', true
FROM edge_dispatcher_context;
INSERT INTO public.register (id, tenant_id, branch_id, name, is_active)
SELECT register_id, tenant_id, branch_id, 'Edge register', true
FROM edge_dispatcher_context;
INSERT INTO public.user_assignment (
  user_id, tenant_id, membership_id, branch_id, role_id, is_active
)
SELECT cashier_id, tenant_id, membership_id, branch_id, role_id, true
FROM edge_dispatcher_context;
INSERT INTO public.shift (
  id, tenant_id, branch_id, register_id, opened_by_user_id, status
)
SELECT shift_id, tenant_id, branch_id, register_id, cashier_id, 'open'
FROM edge_dispatcher_context;
INSERT INTO public.tenant_catalog (
  id, tenant_id, brand_name, dispensing_type, base_price, is_active
)
SELECT catalog_id, tenant_id, 'Edge test medicine', 'otc', 10.00, true
FROM edge_dispatcher_context;
INSERT INTO public.batch (
  id, tenant_id, branch_id, catalog_id, batch_number, expires_at,
  purchase_price, sale_price, qty_initial, qty_remaining
)
SELECT batch_id, tenant_id, branch_id, catalog_id, 'EDGE-TEST',
       CURRENT_DATE + 365, 5.00, 10.00, 20.000, 20.000
FROM edge_dispatcher_context;
INSERT INTO public.sync_node (
  id, tenant_id, branch_id, node_kind, mode, status, display_name
)
SELECT cloud_node_id, tenant_id, branch_id, 'cloud', 'cloud_writer',
       'active', 'Edge test cloud writer'
FROM edge_dispatcher_context;
INSERT INTO public.sync_writer_epoch (
  tenant_id, branch_id, writer_epoch, activation_id, writer_node_id,
  allowed_register_id, capability, state, root_source_checksum,
  root_projection_checksum, last_sequence, current_source_checksum,
  current_projection_checksum, bootstrap_snapshot_hash,
  activation_manifest_hash, receipt_baseline_seq, prepared_at,
  activated_at, fenced_at
)
SELECT tenant_id, branch_id, 1, pg_catalog.gen_random_uuid(), cloud_node_id,
       NULL, 'cloud_full', 'fenced', zero_hash, zero_hash, 0, zero_hash,
       zero_hash, zero_hash, zero_hash, 0, now(), now(), now()
FROM edge_dispatcher_context;
INSERT INTO public.sync_node (
  id, tenant_id, branch_id, register_id, node_kind, mode, status,
  display_name, credential_kid, credential_hash, credential_issued_at,
  credential_expires_at, shadow_start_origin_node_id,
  shadow_start_writer_epoch, shadow_start_sequence,
  shadow_start_checksum, shadow_start_projection_checksum
)
SELECT edge_node_id, tenant_id, branch_id, register_id, 'edge',
       'edge_writer', 'active', 'Edge test writer',
       pg_catalog.gen_random_uuid(),
       pg_catalog.encode(public.gen_random_bytes(32), 'hex'),
       now(), now() + interval '1 day', cloud_node_id, 1, 0,
       zero_hash, zero_hash
FROM edge_dispatcher_context;
INSERT INTO public.sync_writer_activation (
  activation_id, tenant_id, branch_id, writer_epoch, writer_node_id,
  allowed_register_id, capability, state, root_source_checksum,
  root_projection_checksum, last_sequence, current_source_checksum,
  current_projection_checksum, previous_writer_epoch,
  previous_terminal_sequence, previous_terminal_source_checksum,
  previous_terminal_projection_checksum, bootstrap_snapshot_hash,
  activation_manifest_hash, receipt_baseline_seq, prepare_request_hash,
  prepared_at, ready_at, activated_at
)
SELECT activation_id, tenant_id, branch_id, 2, edge_node_id, register_id,
       'cash_sale_v1', 'activated', zero_hash, zero_hash, 0, zero_hash,
       zero_hash, 1, 0, zero_hash, zero_hash, zero_hash, zero_hash, 0,
       pg_catalog.repeat('d', 64), now(), now(), now()
FROM edge_dispatcher_context;
INSERT INTO public.sync_writer_epoch (
  tenant_id, branch_id, writer_epoch, activation_id, writer_node_id,
  allowed_register_id, capability, state, root_source_checksum,
  root_projection_checksum, last_sequence, current_source_checksum,
  current_projection_checksum, previous_writer_epoch,
  previous_terminal_sequence, previous_terminal_source_checksum,
  previous_terminal_projection_checksum, bootstrap_snapshot_hash,
  activation_manifest_hash, receipt_baseline_seq, prepared_at, activated_at
)
SELECT tenant_id, branch_id, 2, activation_id, edge_node_id, register_id,
       'cash_sale_v1', 'active', zero_hash, zero_hash, 0, zero_hash,
       zero_hash, 1, 0, zero_hash, zero_hash, zero_hash, zero_hash, 0,
       now(), now()
FROM edge_dispatcher_context;
INSERT INTO public.sync_stream (
  tenant_id, branch_id, writer_node_id, writer_epoch, last_sequence,
  current_checksum, current_projection_checksum
)
SELECT tenant_id, branch_id, edge_node_id, 2, 0, zero_hash, zero_hash
FROM edge_dispatcher_context;

ALTER TABLE public.sync_writer_epoch ENABLE TRIGGER USER;
ALTER TABLE public.sync_writer_activation ENABLE TRIGGER USER;
ALTER TABLE public.sync_node ENABLE TRIGGER USER;
ALTER TABLE public.batch ENABLE TRIGGER USER;
ALTER TABLE public.tenant_catalog ENABLE TRIGGER USER;
ALTER TABLE public.shift ENABLE TRIGGER USER;
ALTER TABLE public.register ENABLE TRIGGER USER;
ALTER TABLE public.branch ENABLE TRIGGER USER;
ALTER TABLE public.user_assignment ENABLE TRIGGER USER;
ALTER TABLE public.role_permission ENABLE TRIGGER USER;
ALTER TABLE public.role ENABLE TRIGGER USER;
ALTER TABLE public.tenant_membership ENABLE TRIGGER USER;
ALTER TABLE public.app_user ENABLE TRIGGER USER;
ALTER TABLE public.tenant_settings ENABLE TRIGGER USER;
ALTER TABLE public.tenant ENABLE TRIGGER USER;

DO $create_role$
DECLARE
  context_row RECORD;
  database_role TEXT;
BEGIN
  SELECT * INTO context_row FROM edge_dispatcher_context;
  database_role := 'aurum_edge_node_'
    || pg_catalog.replace(context_row.edge_node_id::TEXT, '-', '');
  EXECUTE pg_catalog.format(
    'CREATE ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB '
      || 'NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 1 '
      || 'PASSWORD %L',
    database_role,
    context_row.role_password
  );
  EXECUTE pg_catalog.format(
    'GRANT aurum_edge_cash_executor TO %I '
      || 'WITH ADMIN FALSE, INHERIT TRUE, SET FALSE',
    database_role
  );
  EXECUTE pg_catalog.format(
    'GRANT CONNECT ON DATABASE %I TO %I',
    pg_catalog.current_database(),
    database_role
  );
END
$create_role$;

INSERT INTO public.edge_cash_node_identity (
  tenant_id, branch_id, edge_node_id, register_id,
  database_role, database_role_oid
)
SELECT tenant_id, branch_id, edge_node_id, register_id,
       'aurum_edge_node_' || pg_catalog.replace(edge_node_id::TEXT, '-', ''),
       (
         SELECT role.oid
         FROM pg_catalog.pg_roles AS role
         WHERE role.rolname = 'aurum_edge_node_'
           || pg_catalog.replace(edge_node_id::TEXT, '-', '')
       )
FROM edge_dispatcher_context;

SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE public.sync_stream ENABLE TRIGGER USER;

SELECT pg_catalog.concat_ws(
  '|', tenant_id, cashier_id, branch_id, register_id, catalog_id,
  batch_id, edge_node_id, activation_id,
  'aurum_edge_node_' || pg_catalog.replace(edge_node_id::TEXT, '-', ''),
  role_password
)
FROM edge_dispatcher_context;
COMMIT;
SQL
)"

old_ifs=$IFS
IFS='|'
set -- $scaffold
IFS=$old_ifs
tenant_id=$1
cashier_id=$2
branch_id=$3
register_id=$4
catalog_id=$5
batch_id=$6
edge_node_id=$7
activation_id=$8
edge_role=$9
shift 9
edge_password=$1

request_hash() {
    psql_super \
        -v activation_id="$activation_id" \
        -v cashier_id="$cashier_id" \
        -v catalog_id="$catalog_id" \
        -v qty="$1" \
        -v paid="$2" <<'SQL'
SELECT pg_catalog.encode(
  pg_catalog.sha256(pg_catalog.convert_to(
    public.edge_canonical_jsonb(pg_catalog.jsonb_build_object(
      'activation_id', :'activation_id',
      'cashier_user_id', :'cashier_id',
      'command_type', 'sale.cash.complete',
      'items', pg_catalog.jsonb_build_array(
        pg_catalog.jsonb_build_array(:'catalog_id', :'qty')
      ),
      'paid_amount', :'paid',
      'writer_epoch', 2
    )),
    'UTF8'
  )),
  'hex'
);
SQL
}

edge_call() {
    operation_id=$1
    qty=$2
    paid=$3
    command_hash=$4
    PGPASSWORD="$edge_password" psql -v ON_ERROR_STOP=1 -qAt \
        --host "$postgres_host" \
        --port "$postgres_port" \
        --username "$edge_role" \
        --dbname "$POSTGRES_DB" \
        -v operation_id="$operation_id" \
        -v activation_id="$activation_id" \
        -v cashier_id="$cashier_id" \
        -v catalog_id="$catalog_id" \
        -v qty="$qty" \
        -v paid="$paid" \
        -v command_hash="$command_hash" <<'SQL'
SELECT (result.payload ->> 'sale_id')
  || '|' || (result.payload ->> 'receipt_number')
  || '|' || (result.payload ->> 'total_amount')
FROM public.dispatch_edge_cash_sale_v1(
  :'operation_id'::UUID,
  :'activation_id'::UUID,
  2,
  :'cashier_id'::UUID,
  pg_catalog.jsonb_build_object(
    'items', pg_catalog.jsonb_build_array(
      pg_catalog.jsonb_build_object(
        'catalog_id', :'catalog_id',
        'qty', :'qty'
      )
    ),
    'paid_amount', :'paid'
  ),
  :'command_hash'
) AS result(payload);
SQL
}

operation_one="$(psql_super -c 'SELECT pg_catalog.gen_random_uuid()')"
hash_two_items="$(request_hash '2' '20.00')"
first_result="$(edge_call "$operation_one" '2' '20.00' "$hash_two_items")"
second_result="$(edge_call "$operation_one" '2' '20.00' "$hash_two_items")"
test "$first_result" = "$second_result"

test "$(psql_super \
    -v tenant_id="$tenant_id" \
    -v operation_id="$operation_one" \
    -v batch_id="$batch_id" <<'SQL'
SELECT (
  SELECT pg_catalog.count(*) FROM public.sale
  WHERE tenant_id = :'tenant_id'::UUID
    AND operation_id = :'operation_id'::UUID
) || '|' || (
  SELECT pg_catalog.count(*) FROM public.edge_cash_command
  WHERE tenant_id = :'tenant_id'::UUID
    AND operation_id = :'operation_id'::UUID
) || '|' || (
  SELECT pg_catalog.count(*) FROM public.sync_outbox
  WHERE tenant_id = :'tenant_id'::UUID
    AND operation_id = :'operation_id'::UUID
) || '|' || (
  SELECT qty_remaining FROM public.batch WHERE id = :'batch_id'::UUID
);
SQL
)" = "1|1|1|18.000"

bad_operation="$(psql_super -c 'SELECT pg_catalog.gen_random_uuid()')"
bad_hash="$(request_hash '1' '9.00')"
if edge_call "$bad_operation" '1' '9.00' "$bad_hash" >/dev/null 2>&1; then
    echo "Dispatcher accepted a payment total that does not match the sale" >&2
    exit 1
fi
test "$(psql_super \
    -v tenant_id="$tenant_id" \
    -v operation_id="$bad_operation" \
    -v batch_id="$batch_id" <<'SQL'
SELECT (
  SELECT pg_catalog.count(*) FROM public.sale
  WHERE tenant_id = :'tenant_id'::UUID
    AND operation_id = :'operation_id'::UUID
) || '|' || (
  SELECT pg_catalog.count(*) FROM public.edge_cash_command
  WHERE tenant_id = :'tenant_id'::UUID
    AND operation_id = :'operation_id'::UUID
) || '|' || (
  SELECT qty_remaining FROM public.batch WHERE id = :'batch_id'::UUID
);
SQL
)" = "0|0|18.000"

operation_two="$(psql_super -c 'SELECT pg_catalog.gen_random_uuid()')"
hash_one_item="$(request_hash '1' '10.00')"
edge_call "$operation_two" '1' '10.00' "$hash_one_item" >/dev/null
test "$(psql_super \
    -v tenant_id="$tenant_id" \
    -v operation_id="$operation_two" \
    -v batch_id="$batch_id" <<'SQL'
SELECT (
  SELECT pg_catalog.count(*) FROM public.sale
  WHERE tenant_id = :'tenant_id'::UUID
    AND operation_id = :'operation_id'::UUID
) || '|' || (
  SELECT pg_catalog.count(*) FROM public.edge_cash_command
  WHERE tenant_id = :'tenant_id'::UUID
    AND operation_id = :'operation_id'::UUID
) || '|' || (
  SELECT qty_remaining FROM public.batch WHERE id = :'batch_id'::UUID
);
SQL
)" = "1|1|17.000"

conflict_hash="$(request_hash '2' '20.00')"
if edge_call "$operation_two" '2' '20.00' "$conflict_hash" >/dev/null 2>&1; then
    echo "Dispatcher accepted a reused operation ID with another request" >&2
    exit 1
fi

psql_super -v edge_node_id="$edge_node_id" <<'SQL'
UPDATE public.sync_node SET status = 'revoked'
WHERE id = :'edge_node_id'::UUID;
SQL
revoked_operation="$(psql_super -c 'SELECT pg_catalog.gen_random_uuid()')"
if edge_call "$revoked_operation" '1' '10.00' "$hash_one_item" >/dev/null 2>&1; then
    echo "Dispatcher accepted a command from a revoked Edge node" >&2
    exit 1
fi

psql_super -v edge_role="$edge_role" <<'SQL'
ALTER ROLE :"edge_role" NOLOGIN PASSWORD NULL;
REVOKE CONNECT ON DATABASE :"DBNAME" FROM :"edge_role";
SQL
if PGPASSWORD="$edge_password" psql -qAt \
    --host "$postgres_host" \
    --port "$postgres_port" \
    --username "$edge_role" \
    --dbname "$POSTGRES_DB" \
    -c 'SELECT 1' >/dev/null 2>&1; then
    echo "Revoked Edge database identity can still connect" >&2
    exit 1
fi

test "$(psql_super -v edge_role="$edge_role" <<'SQL'
SELECT NOT role.rolcanlogin
  AND role.rolpassword IS NULL
  AND NOT pg_catalog.has_database_privilege(
    role.rolname,
    pg_catalog.current_database(),
    'CONNECT'
  )
FROM pg_catalog.pg_authid AS role
WHERE role.rolname = :'edge_role';
SQL
)" = "t"

echo "Edge cash dispatcher security and atomicity checks passed."
