#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD_FILE:?POSTGRES_PASSWORD_FILE is required}"

[ "${CI:-}" = "true" ] \
    && { [ "${GITHUB_ACTIONS:-}" = "true" ] || [ "${CIRCLECI:-}" = "true" ]; } \
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
umask 077
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
DROP FUNCTION IF EXISTS public.edge_inputs_ci_catalog_update(uuid);
DROP FUNCTION IF EXISTS public.edge_inputs_ci_lot_update(uuid);
DROP FUNCTION IF EXISTS public.edge_inputs_ci_pack_update(uuid);
DROP FUNCTION IF EXISTS public.edge_draft_ci_unit_update(uuid);
DROP FUNCTION IF EXISTS public.edge_draft_ci_rx_update(uuid);
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
DELETE FROM public.pos_unit_payment_plan_binding_cancel WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.pos_unit_payment_plan_binding WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.pos_unit_payment_plan_intent WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.prescription_log WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.pos_unit_cash_intent WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.pos_unit_cart_item WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.unit_stock_movement_v2 WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.unit_stock_opened_pack_v2 WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.unit_stock_cart_line_v2 WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.unit_stock_cart_command_v2 WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.unit_stock_entry_v2 WHERE tenant_id = :'tenant_id'::UUID;
UPDATE public.incoming_item SET created_unit_lot_v2_id=NULL WHERE tenant_id=:'tenant_id'::UUID;
DELETE FROM public.unit_stock_lot_v2 WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.unit_stock_operation_v2 WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.incoming_item WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.incoming_document WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.supplier WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.user_assignment WHERE tenant_id = :'tenant_id'::UUID;
DELETE FROM public.access_role_version_permission
WHERE role_version_id IN (
  SELECT id FROM public.access_role_version
  WHERE tenant_id = :'tenant_id'::UUID
);
DELETE FROM public.access_role_version
WHERE tenant_id = :'tenant_id'::UUID;
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
  pg_catalog.gen_random_uuid() AS role_version_id,
  pg_catalog.gen_random_uuid() AS branch_id,
  pg_catalog.gen_random_uuid() AS register_id,
  pg_catalog.gen_random_uuid() AS shift_id,
  pg_catalog.gen_random_uuid() AS catalog_id,
  pg_catalog.gen_random_uuid() AS retail_catalog_id,
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
-- The fixture disables tenant triggers, including the default-policy seed.
INSERT INTO public.tenant_access_policy (tenant_id)
SELECT tenant_id FROM edge_dispatcher_context;
INSERT INTO public.tenant_settings (tenant_id, pos_payment_methods)
SELECT tenant_id, '["cash","card"]'::JSONB FROM edge_dispatcher_context;
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
SELECT role_id, permission_code FROM edge_dispatcher_context
CROSS JOIN (VALUES ('pos.sell'),('incoming.finalize'),('pos.handle_prescription')) permissions(permission_code);
INSERT INTO public.access_role_version (
  id, role_id, tenant_id, version, name, status, creation_xid, published_at
)
SELECT role_version_id, role_id, tenant_id, 1, 'Edge cashier role',
       'published', pg_catalog.txid_current(), now()
FROM edge_dispatcher_context;
INSERT INTO public.access_role_version_permission (
  role_version_id, permission_code
)
SELECT role_version_id, permission_code FROM edge_dispatcher_context
CROSS JOIN (VALUES ('pos.sell'),('incoming.finalize'),('pos.handle_prescription')) permissions(permission_code);
INSERT INTO public.branch (id, tenant_id, name, is_active)
SELECT branch_id, tenant_id, 'Edge branch', true
FROM edge_dispatcher_context;
INSERT INTO public.register (id, tenant_id, branch_id, name, is_active)
SELECT register_id, tenant_id, branch_id, 'Edge register', true
FROM edge_dispatcher_context;
INSERT INTO public.user_assignment (
  user_id, tenant_id, membership_id, branch_id, role_id, role_version_id,
  is_active
)
SELECT cashier_id, tenant_id, membership_id, branch_id, role_id,
       role_version_id, true
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
INSERT INTO public.tenant_catalog (
  id, tenant_id, brand_name, dispensing_type, base_price, is_active,
  unit_sale_preparation
)
SELECT retail_catalog_id, tenant_id, 'Edge retail preparation medicine', 'otc', 10.00, true,
       '{"version":1,"unit":"tablet","units_per_pack":10,"units_per_blister":5,
         "dispense_step":1,"state":"prepared","unit_price":"1.00"}'::JSONB
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
       NULL, 'cloud_full', 'active', zero_hash, zero_hash, 0, zero_hash,
       zero_hash, zero_hash, zero_hash, 0, now(), now(), NULL
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
INSERT INTO public.sync_stream (
  tenant_id, branch_id, writer_node_id, writer_epoch, last_sequence,
  current_checksum, current_projection_checksum
)
SELECT tenant_id, branch_id, cloud_node_id, 1, 0, zero_hash, zero_hash
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
  role_password, retail_catalog_id
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
retail_catalog_id=$2

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

# Receive real physical stock through the same guarded journal protocol as the
# app service. All source/journal writes retain their existing triggers. The
# second catalog is deliberately absent from the cart and must never leak.
physical_stock="$(psql_super \
    -v tenant_id="$tenant_id" -v cashier_id="$cashier_id" \
    -v branch_id="$branch_id" -v edge_node_id="$edge_node_id" \
    -v retail_catalog_id="$retail_catalog_id" <<'SQL'
BEGIN;
CREATE TEMP TABLE edge_inputs_receiving ON COMMIT DROP AS
SELECT which, :'tenant_id'::UUID AS tenant_id, :'branch_id'::UUID AS branch_id,
  :'cashier_id'::UUID AS cashier_id,
  CASE which WHEN 'cart' THEN :'retail_catalog_id'::UUID ELSE gen_random_uuid() END AS catalog_id,
  gen_random_uuid() AS supplier_id,gen_random_uuid() AS document_id,
  gen_random_uuid() AS item_id,gen_random_uuid() AS command_id,gen_random_uuid() AS line_id,
  NULL::UUID AS operation_id,NULL::UUID AS lot_id,NULL::UUID AS pack_id,
  NULL::TEXT AS receive_hash,NULL::TEXT AS cart_hash,
  '{"version":1,"unit":"tablet","units_per_pack":10,"dispense_step":1}'::JSONB AS packaging_v1,
  '{"version":2,"unit":"tablet","units_per_pack":10,
    "units_per_blister":5,"dispense_step":1}'::JSONB AS packaging_v2
FROM (VALUES ('cart'),('unrelated')) names(which);
GRANT SELECT ON edge_inputs_receiving TO aurum_app;
SET SESSION AUTHORIZATION aurum_app;
SELECT set_config('app.tenant_id',:'tenant_id',true),
  set_config('app.branch_id',:'branch_id',true),set_config('app.user_id',:'cashier_id',true),
  set_config('app.edge_node_id','',true)
\gset
INSERT INTO public.tenant_catalog(id,tenant_id,brand_name,dispensing_type,base_price,
  is_active,unit_sale_preparation)
SELECT catalog_id,tenant_id,'Unrelated physical stock','otc',10,true,
  '{"version":1,"unit":"tablet","units_per_pack":10,"units_per_blister":5,
    "dispense_step":1,"state":"prepared","unit_price":"1.00"}'::JSONB
FROM edge_inputs_receiving WHERE which='unrelated';
INSERT INTO public.supplier(id,tenant_id,operation_id,create_request_fingerprint,name,created_by)
SELECT supplier_id,tenant_id,gen_random_uuid(),repeat('a',64),'Native inputs test supplier',cashier_id
FROM edge_inputs_receiving;
INSERT INTO public.incoming_document(id,tenant_id,operation_id,branch_id,supplier_id,
  document_date,total_amount,created_by)
SELECT document_id,tenant_id,gen_random_uuid(),branch_id,supplier_id,CURRENT_DATE,10,cashier_id
FROM edge_inputs_receiving;
INSERT INTO public.incoming_item(id,tenant_id,operation_id,document_id,catalog_id,
  expires_at,qty,purchase_price,sale_price,unit_packaging_preparation,unit_packaging_snapshot_v2)
SELECT item_id,tenant_id,gen_random_uuid(),document_id,catalog_id,CURRENT_DATE+365,
  2,5,10,packaging_v1,packaging_v2 FROM edge_inputs_receiving;
RESET SESSION AUTHORIZATION;
-- Private calculations are fixture values, not grants to the app identity.
UPDATE edge_inputs_receiving SET
  operation_id=public.unit_incoming_uuid5('aurum-unit-incoming-v2:'||tenant_id||':'||item_id),
  receive_hash=public.unit_incoming_v2_fingerprint(tenant_id,item_id);
UPDATE edge_inputs_receiving SET
  lot_id=public.unit_incoming_uuid5('aurum-unit-lot-v2:'||tenant_id||':'||operation_id);
UPDATE edge_inputs_receiving SET
  pack_id=public.unit_incoming_uuid5('aurum-unit-open-v2:'||tenant_id||':'||command_id||':'||lot_id||':1'),
  cart_hash=encode(sha256(convert_to(replace(json_build_array('aurum-unit-cart-v2:1',
    tenant_id::TEXT,branch_id::TEXT,json_build_array(json_build_array(line_id::TEXT,
      catalog_id::TEXT,2,'tablet',10,5,1,'unit',1)))::TEXT,' ',''),'UTF8')),'hex');
SET SESSION AUTHORIZATION aurum_app;
INSERT INTO public.unit_stock_operation_v2(tenant_id,id,branch_id,catalog_id,source_item_id,
  packaging,kind,sale_format,quantity,base_units,request_hash,created_by)
SELECT tenant_id,operation_id,branch_id,catalog_id,item_id,packaging_v2,
  'initialize','pack',2,20,receive_hash,cashier_id FROM edge_inputs_receiving;
INSERT INTO public.unit_stock_lot_v2(tenant_id,id,branch_id,catalog_id,source_item_id,
  packaging,expires_at,initial_closed_packs,closed_packs,purchase_cost_per_pack,cost_version,created_by)
SELECT tenant_id,lot_id,branch_id,catalog_id,item_id,packaging_v2,CURRENT_DATE+365,
  2,0,5,1,cashier_id FROM edge_inputs_receiving;
UPDATE public.incoming_item item SET created_unit_lot_v2_id=context.lot_id
FROM edge_inputs_receiving context WHERE item.id=context.item_id AND item.tenant_id=context.tenant_id;
INSERT INTO public.unit_stock_entry_v2(tenant_id,id,branch_id,catalog_id,source_item_id,
  operation_id,lot_id,kind,closed_delta,base_units,created_by)
SELECT tenant_id,gen_random_uuid(),branch_id,catalog_id,item_id,operation_id,lot_id,
  'initialize',2,20,cashier_id FROM edge_inputs_receiving;
UPDATE public.incoming_document document SET status='accepted',accepted_at=statement_timestamp(),
  accepted_by=context.cashier_id
FROM edge_inputs_receiving context WHERE document.id=context.document_id;
SET CONSTRAINTS ALL IMMEDIATE;
SET CONSTRAINTS ALL DEFERRED;
INSERT INTO public.unit_stock_cart_command_v2(tenant_id,id,branch_id,actor_id,request_hash,created_by)
SELECT tenant_id,command_id,branch_id,cashier_id,cart_hash,cashier_id
FROM edge_inputs_receiving WHERE which='cart';
INSERT INTO public.unit_stock_cart_line_v2(tenant_id,command_id,line_id,position,catalog_id,
  packaging,sale_format,quantity,base_units,created_by)
SELECT tenant_id,command_id,line_id,0,catalog_id,packaging_v2,'unit',1,1,cashier_id
FROM edge_inputs_receiving WHERE which='cart';
INSERT INTO public.unit_stock_movement_v2(tenant_id,id,branch_id,catalog_id,command_id,
  line_id,lot_id,pack_id,kind,entry_order,closed_delta,units_taken,after_state,
  sealed_start,sealed_count,created_by)
SELECT tenant_id,gen_random_uuid(),branch_id,catalog_id,command_id,line_id,lot_id,pack_id,
  'pack_open',0,-1,0,
  '{"intact_blisters":[1,2],"opened_blisters":[],"loose_units":0}'::JSONB,1,1,cashier_id
FROM edge_inputs_receiving WHERE which='cart';
INSERT INTO public.unit_stock_movement_v2(tenant_id,id,branch_id,catalog_id,command_id,
  line_id,lot_id,pack_id,kind,entry_order,closed_delta,units_taken,before_state,after_state,created_by)
SELECT tenant_id,gen_random_uuid(),branch_id,catalog_id,command_id,line_id,lot_id,pack_id,
  'unit_take',1,0,1,
  '{"intact_blisters":[1,2],"opened_blisters":[],"loose_units":0}'::JSONB,
  '{"intact_blisters":[2],"opened_blisters":[{"number":1,"remaining_units":4}],"loose_units":0}'::JSONB,
  cashier_id FROM edge_inputs_receiving WHERE which='cart';
SET CONSTRAINTS ALL IMMEDIATE;
RESET SESSION AUTHORIZATION;
SELECT lot_id||'|'||pack_id FROM edge_inputs_receiving WHERE which='cart';
COMMIT;
SQL
)"
old_ifs=$IFS
IFS='|'
set -- $physical_stock
IFS=$old_ifs
physical_lot_id=$1
physical_pack_id=$2

# Seed immutable confirmations through their real app-role guards. The native
# preparation below uses a separate authenticated per-node LOGIN connection.
retail_payloads="$(psql_super \
    -v tenant_id="$tenant_id" \
    -v cashier_id="$cashier_id" \
    -v branch_id="$branch_id" \
    -v register_id="$register_id" \
    -v catalog_id="$catalog_id" \
    -v retail_catalog_id="$retail_catalog_id" \
    -v batch_id="$batch_id" \
    -v edge_node_id="$edge_node_id" \
    -v activation_id="$activation_id" <<'SQL'
BEGIN;
CREATE TEMP TABLE retail_preparation_context ON COMMIT DROP AS
SELECT kind,binding_mode,pg_catalog.gen_random_uuid() AS plan_id,
       pg_catalog.gen_random_uuid() AS sale_id,
       pg_catalog.gen_random_uuid() AS line_id,
       pg_catalog.gen_random_uuid() AS legacy_id,
       pg_catalog.gen_random_uuid() AS operation_id,
       :'tenant_id'::UUID AS tenant_id, :'cashier_id'::UUID AS cashier_id,
       :'branch_id'::UUID AS branch_id, :'register_id'::UUID AS register_id,
       :'catalog_id'::UUID AS catalog_id, :'batch_id'::UUID AS batch_id,
       :'retail_catalog_id'::UUID AS retail_catalog_id,
       :'edge_node_id'::UUID AS edge_node_id, :'activation_id'::UUID AS activation_id,
       (SELECT id FROM public.shift WHERE tenant_id=:'tenant_id'::UUID
         AND register_id=:'register_id'::UUID AND status='open') AS shift_id
FROM (VALUES ('physical_cash','none'),('mixed_cash','none'),
  ('physical_cash','active'),('physical_cash','cancelled')) kinds(kind,binding_mode);
GRANT SELECT ON retail_preparation_context TO aurum_app;
SET SESSION AUTHORIZATION aurum_app;
SELECT set_config('app.tenant_id', :'tenant_id', true) AS tenant_scope,
       set_config('app.branch_id', :'branch_id', true) AS branch_scope,
       set_config('app.user_id', :'cashier_id', true) AS cashier_scope,
       set_config('app.edge_node_id', '', true) AS writer_scope
\gset
INSERT INTO public.sale(id,tenant_id,branch_id,register_id,shift_id,cashier_user_id)
SELECT sale_id,tenant_id,branch_id,register_id,shift_id,cashier_id
FROM retail_preparation_context;
INSERT INTO public.sale_item(id,tenant_id,sale_id,catalog_id,batch_id,qty,
  unit_price,total_price,currency,discount_amount,position)
SELECT legacy_id,tenant_id,sale_id,catalog_id,batch_id,1,10,10,'TJS',0,1
FROM retail_preparation_context WHERE kind='mixed_cash';
UPDATE public.sale SET total_amount=10
WHERE id IN (SELECT sale_id FROM retail_preparation_context WHERE kind='mixed_cash');
INSERT INTO public.pos_unit_cart_item(id,tenant_id,branch_id,sale_id,catalog_id,
  position,sale_format,physical_packaging,quantity,discount_amount)
SELECT line_id,tenant_id,branch_id,sale_id,retail_catalog_id,1,'unit',
  '{"version":2,"unit":"tablet","units_per_pack":10,"units_per_blister":5,
    "dispense_step":1}'::JSONB,1,0
FROM retail_preparation_context;
RESET SESSION AUTHORIZATION;
-- Fingerprints are fixture calculations, not new privileges for aurum_app.
CREATE TEMP TABLE retail_preparation_quotes ON COMMIT DROP AS
SELECT context.*,jsonb_build_object('schema_version',2,'state','draft_quote','tenant_id',tenant_id,
    'branch_id',branch_id,'sale_id',sale_id,'register_id',register_id,'shift_id',shift_id,
    'cashier_id',cashier_id,'lines',
    CASE kind WHEN 'mixed_cash' THEN jsonb_build_array(jsonb_build_object(
      'inventory_kind','legacy_pack','id',legacy_id,'position',1,'catalog_id',catalog_id,
      'batch_id',batch_id,'quantity','1.000','original_retail_unit_price','10.00',
      'gross_amount','10.00','discount_amount','0.00','net_amount','10.00',
      'money_version',1,'currency','TJS')) ELSE '[]'::JSONB END
    ||jsonb_build_array(jsonb_build_object('inventory_kind','physical_unit','id',line_id,
      'position',CASE kind WHEN 'mixed_cash' THEN 2 ELSE 1 END,'catalog_id',retail_catalog_id,
      'packaging',jsonb_build_object('version',2,'unit','tablet','units_per_pack',10,
        'units_per_blister',5,'dispense_step',1),'sale_format','unit','quantity',1,
      'base_units',1,'original_retail_unit_price','1.00','gross_amount','1.00',
      'discount_amount','0.00','net_amount','1.00','money_version',1,'currency','TJS')),
    'total_amount',CASE kind WHEN 'mixed_cash' THEN '11.00' ELSE '1.00' END,'currency','TJS') AS quote
FROM retail_preparation_context context;
ALTER TABLE retail_preparation_quotes ADD COLUMN revision_hash TEXT,
 ADD COLUMN cash_request_hash TEXT;
UPDATE retail_preparation_quotes SET revision_hash=
 encode(sha256(convert_to(public.edge_canonical_jsonb(quote),'UTF8')),'hex');
UPDATE retail_preparation_quotes SET cash_request_hash=
 encode(sha256(convert_to('aurum-cash-intent-v1:'||public.edge_canonical_jsonb(
   jsonb_build_object('schema_version',2,'operation_id',operation_id,'sale_id',sale_id,
     'expected_revision_hash',revision_hash,
     'expected_total_amount',CASE kind WHEN 'mixed_cash' THEN '11.00' ELSE '1.00' END,
     'cash_tendered',CASE kind WHEN 'mixed_cash' THEN '16.00' ELSE '6.00' END,
     'currency','TJS')),'UTF8')),'hex');
GRANT SELECT ON retail_preparation_quotes TO aurum_app;
SET SESSION AUTHORIZATION aurum_app;
INSERT INTO public.pos_unit_cash_intent(tenant_id,id,branch_id,sale_id,register_id,
  shift_id,cashier_user_id,request_hash,revision_hash,expected_total_amount,
  cash_tendered,change_amount,currency,quote_payload,created_by)
SELECT tenant_id,operation_id,branch_id,sale_id,register_id,shift_id,cashier_id,
  cash_request_hash,revision_hash,
  CASE kind WHEN 'mixed_cash' THEN 11 ELSE 1 END,
  CASE kind WHEN 'mixed_cash' THEN 16 ELSE 6 END,5,'TJS',quote,cashier_id
FROM retail_preparation_quotes;
RESET SESSION AUTHORIZATION;
CREATE TEMP TABLE retail_preparation_plans ON COMMIT DROP AS
WITH requests AS (
 SELECT context.*,intent.quote_payload AS quote,intent.revision_hash AS revision,
   jsonb_build_object('sale_id',context.sale_id,
     'expected_revision_hash',intent.revision_hash,
     'expected_total_amount',intent.quote_payload->'total_amount','currency','TJS',
     'payments',jsonb_build_array(jsonb_build_object(
       'method','card','amount',intent.quote_payload->'total_amount')),
     'cash_tendered',NULL) AS request
 FROM retail_preparation_context context
 JOIN public.pos_unit_cash_intent intent ON intent.tenant_id=context.tenant_id
   AND intent.id=context.operation_id
 WHERE context.binding_mode IN ('active','cancelled')
), plans AS (
 SELECT requests.*,jsonb_build_object('kind','unit_payment_plan_v1',
   'tenant_id',tenant_id,'branch_id',branch_id,'cashier_id',cashier_id,
   'sale_id',sale_id,'revision_hash',revision,'currency','TJS',
   'payments',request->'payments','cash_tendered',NULL) AS plan
 FROM requests
)
SELECT plans.*,
 encode(sha256(convert_to(public.edge_canonical_jsonb(jsonb_build_object(
   'kind','unit_payment_intent_request_v1','request',request)),'UTF8')),'hex') AS request_hash,
 encode(sha256(convert_to(public.edge_canonical_jsonb(plan),'UTF8')),'hex') AS plan_hash
FROM plans;
GRANT SELECT ON retail_preparation_plans TO aurum_app;
SET SESSION AUTHORIZATION aurum_app;
INSERT INTO public.pos_unit_payment_plan_intent(
 tenant_id,id,branch_id,sale_id,register_id,shift_id,cashier_user_id,
 request_hash,plan_hash,request_payload,change_amount,quote_payload,created_by)
SELECT tenant_id,plan_id,branch_id,sale_id,register_id,shift_id,cashier_id,
 request_hash,plan_hash,request,0,quote,cashier_id FROM retail_preparation_plans;
INSERT INTO public.pos_unit_payment_plan_binding(
 tenant_id,id,sale_id,branch_id,cashier_user_id,created_by)
SELECT tenant_id,plan_id,sale_id,branch_id,cashier_id,cashier_id
FROM retail_preparation_context WHERE binding_mode IN ('active','cancelled');
INSERT INTO public.pos_unit_payment_plan_binding_cancel(
 tenant_id,id,sale_id,branch_id,cashier_user_id,created_by)
SELECT tenant_id,plan_id,sale_id,branch_id,cashier_id,cashier_id
FROM retail_preparation_context WHERE binding_mode='cancelled';
RESET SESSION AUTHORIZATION;
CREATE TEMP TABLE retail_preparation_payloads ON COMMIT DROP AS
SELECT kind,binding_mode,jsonb_build_object('profile','retail_cash.prepare.v1','kind',kind,
  'scope',jsonb_build_object('activation_id',activation_id,'tenant_id',tenant_id,
    'branch_id',branch_id,'edge_node_id',edge_node_id,'register_id',register_id,
    'writer_epoch',2,'user_id',cashier_id,'capability','cash_sale_v1'),
  'confirmation',jsonb_build_object('schema_version',2,'operation_id',operation_id,
    'sale_id',sale_id,'expected_revision_hash',(SELECT intent.revision_hash
      FROM public.pos_unit_cash_intent intent WHERE intent.id=operation_id
        AND intent.tenant_id=retail_preparation_context.tenant_id),
    'expected_total_amount',CASE kind WHEN 'mixed_cash' THEN '11.00' ELSE '1.00' END,
    'cash_tendered',CASE kind WHEN 'mixed_cash' THEN '16.00' ELSE '6.00' END,'currency','TJS')) AS payload
FROM retail_preparation_context;
SELECT payload FROM retail_preparation_payloads WHERE binding_mode='none' ORDER BY kind;
\o /tmp/aurum-native-bindings.jsonl
SELECT jsonb_build_object('mode',binding_mode,'payload',payload)
FROM retail_preparation_payloads WHERE binding_mode<>'none' ORDER BY binding_mode;
\o
COMMIT;
SQL
)"

# Receiving and draft/payment-plan setup above ran under the real cloud writer. Only now assemble the
# synthetic Edge activation, using the same fixture-only trigger suspension
# as initial writer provisioning. Product guards remain unchanged.
psql_super -v tenant_id="$tenant_id" -v branch_id="$branch_id" \
    -v edge_node_id="$edge_node_id" -v register_id="$register_id" \
    -v activation_id="$activation_id" <<'SQL'
BEGIN;
CREATE TEMP TABLE edge_dispatcher_context ON COMMIT DROP AS
SELECT :'tenant_id'::UUID AS tenant_id,:'branch_id'::UUID AS branch_id,
  :'edge_node_id'::UUID AS edge_node_id,:'register_id'::UUID AS register_id,
  :'activation_id'::UUID AS activation_id,repeat('0',64) AS zero_hash;
ALTER TABLE public.sync_writer_activation DISABLE TRIGGER USER;
ALTER TABLE public.sync_writer_epoch DISABLE TRIGGER USER;
ALTER TABLE public.sync_stream DISABLE TRIGGER USER;
UPDATE public.sync_writer_epoch SET state='fenced',fenced_at=statement_timestamp()
WHERE tenant_id=:'tenant_id'::UUID AND branch_id=:'branch_id'::UUID AND writer_epoch=1;
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
UPDATE public.sync_stream SET writer_node_id=:'edge_node_id'::UUID,writer_epoch=2
WHERE tenant_id=:'tenant_id'::UUID AND branch_id=:'branch_id'::UUID;
-- Resolve the deferred stream foreign keys before changing trigger metadata.
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE public.sync_stream ENABLE TRIGGER USER;
ALTER TABLE public.sync_writer_epoch ENABLE TRIGGER USER;
ALTER TABLE public.sync_writer_activation ENABLE TRIGGER USER;
COMMIT;
SQL


retail_hash() {
    psql_super -v payload="$1" <<'SQL'
SELECT encode(sha256(convert_to(public.edge_canonical_jsonb(:'payload'::JSONB),'UTF8')),'hex');
SQL
}

retail_call() {
    PGPASSWORD="$edge_password" psql -v ON_ERROR_STOP=1 -v VERBOSITY="${3:-default}" -qAt \
        --host "$postgres_host" --port "$postgres_port" \
        --username "$edge_role" --dbname "$POSTGRES_DB" \
        -v payload="$1" -v request_hash="$2" <<'SQL'
-- GUC values are deliberately untrusted. Binding must come from SESSION_USER.
BEGIN;
SELECT set_config('app.tenant_id','00000000-0000-4000-8000-000000000000',true) AS spoof_tenant,
       set_config('app.edge_node_id','00000000-0000-4000-8000-000000000000',true) AS spoof_node
\gset
SELECT public.dispatch_edge_cash_sale_v1(
  (:'payload'::JSONB->'confirmation'->>'operation_id')::UUID,
  (:'payload'::JSONB->'scope'->>'activation_id')::UUID,
  (:'payload'::JSONB->'scope'->>'writer_epoch')::BIGINT,
  (:'payload'::JSONB->'scope'->>'user_id')::UUID,:'payload'::JSONB,:'request_hash');
COMMIT;
SQL
}

retail_facts() {
    psql_super -v tenant_id="$tenant_id" <<'SQL'
SELECT jsonb_build_object(
  'sales',(SELECT jsonb_agg(to_jsonb(row) ORDER BY id) FROM public.sale row
    WHERE tenant_id=:'tenant_id'::UUID),
  'batch',(SELECT jsonb_agg(to_jsonb(row) ORDER BY id) FROM public.batch row
    WHERE tenant_id=:'tenant_id'::UUID),
  'shift',(SELECT jsonb_agg(to_jsonb(row) ORDER BY id) FROM public.shift row
    WHERE tenant_id=:'tenant_id'::UUID),
  'payments',(SELECT count(*) FROM public.sale_payment WHERE tenant_id=:'tenant_id'::UUID),
  'unit_items',(SELECT jsonb_agg(to_jsonb(row) ORDER BY id) FROM public.pos_unit_cart_item row
    WHERE tenant_id=:'tenant_id'::UUID),
  'legacy_items',(SELECT jsonb_agg(jsonb_build_object('id',id,'catalog_id',catalog_id,
    'batch_id',batch_id,'qty',qty,'unit_price',unit_price,'discount_amount',discount_amount,
    'total_price',total_price,'position',position) ORDER BY id)
    FROM public.sale_item WHERE tenant_id=:'tenant_id'::UUID),
  'rx_links',(SELECT jsonb_agg(jsonb_build_object('id',id,'sale_id',sale_id,
    'sale_item_id',sale_item_id,'unit_cart_item_id',unit_cart_item_id) ORDER BY id)
    FROM public.prescription_log WHERE tenant_id=:'tenant_id'::UUID),
  'payment_plans',(SELECT count(*) FROM public.pos_unit_payment_plan_intent
    WHERE tenant_id=:'tenant_id'::UUID),
  'payment_bindings',(SELECT count(*) FROM public.pos_unit_payment_plan_binding
    WHERE tenant_id=:'tenant_id'::UUID),
  'payment_cancellations',(SELECT count(*) FROM public.pos_unit_payment_plan_binding_cancel
    WHERE tenant_id=:'tenant_id'::UUID),
  'commands',(SELECT count(*) FROM public.edge_cash_command WHERE tenant_id=:'tenant_id'::UUID),
  'outbox',(SELECT count(*) FROM public.sync_outbox WHERE tenant_id=:'tenant_id'::UUID),
  'stock_commands',(SELECT count(*) FROM public.unit_stock_cart_command_v2
    WHERE tenant_id=:'tenant_id'::UUID),
  'stock_movements',(SELECT count(*) FROM public.unit_stock_movement_v2
    WHERE tenant_id=:'tenant_id'::UUID),
  'physical_lots',(SELECT jsonb_agg(to_jsonb(row) ORDER BY id)
    FROM public.unit_stock_lot_v2 row WHERE tenant_id=:'tenant_id'::UUID),
  'physical_packs',(SELECT jsonb_agg(to_jsonb(row) ORDER BY id)
    FROM public.unit_stock_opened_pack_v2 row WHERE tenant_id=:'tenant_id'::UUID),
  'physical_checkouts',(SELECT count(*) FROM public.pos_physical_checkout
    WHERE tenant_id=:'tenant_id'::UUID),
  'mixed_checkouts',(SELECT count(*) FROM public.pos_mixed_checkout
    WHERE tenant_id=:'tenant_id'::UUID),
  'stream',(SELECT jsonb_agg(to_jsonb(row) ORDER BY id) FROM public.sync_stream row
    WHERE tenant_id=:'tenant_id'::UUID),
  'receipt_counter',(SELECT jsonb_agg(to_jsonb(row) ORDER BY to_jsonb(row)::TEXT)
    FROM public.register_receipt_counter row WHERE tenant_id=:'tenant_id'::UUID),
  'audit',(SELECT count(*) FROM public.audit_log WHERE tenant_id=:'tenant_id'::UUID)
);
SQL
}

umask 077
draft_fixture_file=/tmp/aurum-edge-draft-fixtures.jsonl
: > "$draft_fixture_file"
retail_before="$(retail_facts)"
inputs_lock_payload=""
old_ifs=$IFS
IFS='
'
for retail_payload in $retail_payloads; do
    retail_request_hash="$(retail_hash "$retail_payload")"
    retail_first="$(retail_call "$retail_payload" "$retail_request_hash")"
    test "$retail_first" = "$(retail_call "$retail_payload" "$retail_request_hash")"
    test "$(psql_super -v result="$retail_first" -v payload="$retail_payload" <<'SQL'
SELECT :'result'::JSONB->>'state'='prepared_not_executed'
  AND :'result'::JSONB->'schema_version'='1'::JSONB
  AND :'result'::JSONB->'scope'=:'payload'::JSONB->'scope'
  AND :'result'::JSONB->'kind'=:'payload'::JSONB->'kind'
  AND :'result'::JSONB->'sale_id'=:'payload'::JSONB->'confirmation'->'sale_id'
  AND :'result'::JSONB->'saved_confirmation'=:'payload'::JSONB->'confirmation'
  AND NOT :'result'::JSONB ?| ARRAY['receipt_number','payments','stock_command_id'];
SQL
)" = "t"
    inputs_payload="$(psql_super -v payload="$retail_payload" <<'SQL'
SELECT jsonb_set(:'payload'::JSONB,'{profile}','"retail_cash.inputs.v1"'::JSONB);
SQL
)"
    inputs_hash="$(retail_hash "$inputs_payload")"
    inputs_result="$(retail_call "$inputs_payload" "$inputs_hash")"
    test "$inputs_result" = "$(retail_call "$inputs_payload" "$inputs_hash")"
    draft_payload="$(psql_super -v payload="$retail_payload" <<'SQL'
SELECT jsonb_set(:'payload'::JSONB,'{profile}','"retail_cash.draft.v1"'::JSONB);
SQL
)"
    draft_hash="$(retail_hash "$draft_payload")"
    draft_result="$(retail_call "$draft_payload" "$draft_hash")"
    test "$draft_result" = "$(retail_call "$draft_payload" "$draft_hash")"
    if draft_denial="$(retail_call "$draft_payload" \
        "$(psql_super -c "SELECT repeat('0',64)")" verbose 2>&1)"; then
        echo "Native current draft accepted an invalid hash" >&2
        exit 1
    fi
    case "$draft_denial" in
        *22023*'Invalid native retail draft envelope'*) ;;
        *) echo "Native current-draft hash refusal has an unexpected cause" >&2; exit 1 ;;
    esac
    test "$(psql_super -v result="$draft_result" -v inputs="$inputs_result" <<'SQL'
SELECT :'result'::JSONB->>'state'='current_draft_not_executed'
 AND :'result'::JSONB->'inputs'=:'inputs'::JSONB
 AND jsonb_array_length(:'result'::JSONB->'unit_items')=1
 AND :'result'::JSONB->'unit_items'->0->>'quantity'='1'
 AND :'result'::JSONB->'rx_refs'='[]'::JSONB
 AND :'result'::TEXT !~ 'prescription_number|doctor_name|doctor_license|patient_name|notes';
SQL
)" = "t"
    psql_super -v result="$draft_result" -v payload="$draft_payload" <<'SQL' >> "$draft_fixture_file"
SELECT jsonb_build_object('case',(:'payload'::JSONB->>'kind')||':baseline',
  'expect','ok','draft',:'result'::JSONB);
SQL
    inputs_lock_payload=${inputs_lock_payload:-$inputs_payload}
    test "$(psql_super -v result="$inputs_result" -v prepared="$retail_first" \
        -v payload="$inputs_payload" -v tenant_id="$tenant_id" -v branch_id="$branch_id" \
        -v retail_catalog_id="$retail_catalog_id" -v catalog_id="$catalog_id" \
        -v lot_id="$physical_lot_id" -v pack_id="$physical_pack_id" -v batch_id="$batch_id" <<'SQL'
WITH input AS (SELECT :'result'::JSONB AS snapshot)
SELECT snapshot->>'state'='locked_inputs_not_executed'
 AND snapshot->'schema_version'='1'::JSONB
 AND snapshot-ARRAY['schema_version','state','preparation','saved_quote','stock_date',
   'catalogs','physical_stock','legacy_stock']='{}'::JSONB
 AND snapshot->'preparation'=:'prepared'::JSONB
 AND snapshot->'saved_quote'=(SELECT quote_payload FROM public.pos_unit_cash_intent
   WHERE tenant_id=:'tenant_id'::UUID AND id=(:'payload'::JSONB->'confirmation'->>'operation_id')::UUID)
 AND snapshot->>'stock_date'=to_char(clock_timestamp() AT TIME ZONE 'Asia/Dushanbe','YYYY-MM-DD')
 AND snapshot->'physical_stock'=jsonb_build_array(jsonb_build_object(
   'id',lot.id,'tenant_id',lot.tenant_id,'branch_id',lot.branch_id,'catalog_id',lot.catalog_id,
   'expires_at',lot.expires_at,'created_at',to_char(lot.created_at AT TIME ZONE 'UTC',
     'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'packaging',lot.packaging,'closed_packs',1,'is_blocked',false,
   'opened_packs',jsonb_build_array(jsonb_build_object('id',pack.id,
     'opened_at',to_char(pack.opened_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
     'intact_blisters',jsonb_build_array(2),
     'opened_blisters',jsonb_build_array(jsonb_build_object('number',1,'remaining_units',4)),
     'loose_units',0))))
 AND (SELECT array_agg(entry->>'id' ORDER BY entry->>'id')
   FROM jsonb_array_elements(snapshot->'catalogs') entry)=CASE
   WHEN :'payload'::JSONB->>'kind'='mixed_cash' THEN
     (SELECT array_agg(id ORDER BY id) FROM (VALUES (:'retail_catalog_id'),(:'catalog_id')) ids(id))
   ELSE ARRAY[:'retail_catalog_id'] END
 AND (SELECT bool_and(entry->>'tenant_id'=:'tenant_id' AND NOT entry ? 'purchase_price')
   FROM jsonb_array_elements(snapshot->'catalogs') entry)
 AND CASE WHEN :'payload'::JSONB->>'kind'='mixed_cash' THEN
   jsonb_array_length(snapshot->'legacy_stock')=1
   AND snapshot->'legacy_stock'->0->>'id'=:'batch_id'
   AND snapshot->'legacy_stock'->0->>'qty_remaining'='20.000'
   AND snapshot->'legacy_stock'->0->>'sale_price'='10.00'
   AND snapshot->'legacy_stock'->0->>'branch_id'=:'branch_id'
 ELSE snapshot->'legacy_stock'='[]'::JSONB END
 AND snapshot::TEXT !~ 'purchase_cost|purchase_price|original_pack_cost|cost_amount'
FROM input CROSS JOIN public.unit_stock_lot_v2 lot
JOIN public.unit_stock_opened_pack_v2 pack ON pack.lot_id=lot.id
WHERE lot.id=:'lot_id'::UUID AND pack.id=:'pack_id'::UUID;
SQL
)" = "t"
    if retail_call "$inputs_payload" "$(psql_super -c "SELECT repeat('0',64)")" >/dev/null 2>&1; then
        echo "Native retail inputs accepted an invalid hash" >&2
        exit 1
    fi
    for retail_mutation in foreign_scope stale_epoch saved_mismatch wrong_kind null_kind; do
        altered_payload="$(psql_super -v payload="$retail_payload" \
            -v mutation="$retail_mutation" <<'SQL'
SELECT CASE :'mutation'
 WHEN 'foreign_scope' THEN jsonb_set(:'payload'::JSONB,'{scope,tenant_id}',
   to_jsonb(gen_random_uuid()))
 WHEN 'stale_epoch' THEN jsonb_set(:'payload'::JSONB,'{scope,writer_epoch}','1')
 WHEN 'saved_mismatch' THEN jsonb_set(:'payload'::JSONB,'{confirmation,expected_revision_hash}',
   to_jsonb(repeat('d',64)))
 WHEN 'null_kind' THEN jsonb_set(:'payload'::JSONB,'{kind}','null')
 ELSE jsonb_set(:'payload'::JSONB,'{kind}',to_jsonb(CASE
   WHEN :'payload'::JSONB->>'kind'='mixed_cash' THEN 'physical_cash' ELSE 'mixed_cash' END)) END;
SQL
)"
        if retail_call "$altered_payload" "$(retail_hash "$altered_payload")" >/dev/null 2>&1; then
            echo "Native retail preparation accepted $retail_mutation" >&2
            exit 1
        fi
        altered_inputs="$(psql_super -v payload="$altered_payload" <<'SQL'
SELECT jsonb_set(:'payload'::JSONB,'{profile}','"retail_cash.inputs.v1"'::JSONB);
SQL
)"
        if retail_call "$altered_inputs" "$(retail_hash "$altered_inputs")" >/dev/null 2>&1; then
            echo "Native retail inputs accepted $retail_mutation" >&2
            exit 1
        fi
        altered_draft="$(psql_super -v payload="$altered_payload" <<'SQL'
SELECT jsonb_set(:'payload'::JSONB,'{profile}','"retail_cash.draft.v1"'::JSONB);
SQL
)"
        if retail_call "$altered_draft" "$(retail_hash "$altered_draft")" >/dev/null 2>&1; then
            echo "Native current draft accepted $retail_mutation" >&2
            exit 1
        fi
    done
done
IFS=$old_ifs
test "$retail_before" = "$(retail_facts)"

# Prove that granted UPDATE(id) permits row locks but cannot mutate a visible
# row. Test-only fixed functions run as the actual dispatcher owner under the
# same SESSION_USER as a node. They accept no SQL or relation names.
psql_super -v edge_role="$edge_role" <<'SQL'
BEGIN;
GRANT CREATE ON SCHEMA public TO aurum_edge_cash_owner;
SET LOCAL ROLE aurum_edge_cash_owner;
CREATE FUNCTION public.edge_inputs_ci_catalog_update(p_id UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
BEGIN
 PERFORM 1 FROM public.tenant_catalog WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'CI row not visible'; END IF;
 UPDATE public.tenant_catalog SET id=id WHERE id=p_id;
 RAISE EXCEPTION 'CI write unexpectedly succeeded';
END $$;
CREATE FUNCTION public.edge_inputs_ci_lot_update(p_id UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
BEGIN
 PERFORM 1 FROM public.unit_stock_lot_v2 WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'CI row not visible'; END IF;
 UPDATE public.unit_stock_lot_v2 SET id=id WHERE id=p_id;
 RAISE EXCEPTION 'CI write unexpectedly succeeded';
END $$;
CREATE FUNCTION public.edge_inputs_ci_pack_update(p_id UUID) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
BEGIN
 PERFORM 1 FROM public.unit_stock_opened_pack_v2 WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'CI row not visible'; END IF;
 UPDATE public.unit_stock_opened_pack_v2 SET id=id WHERE id=p_id;
 RAISE EXCEPTION 'CI write unexpectedly succeeded';
END $$;
REVOKE ALL ON FUNCTION public.edge_inputs_ci_catalog_update(UUID),
 public.edge_inputs_ci_lot_update(UUID),public.edge_inputs_ci_pack_update(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.edge_inputs_ci_catalog_update(UUID),
 public.edge_inputs_ci_lot_update(UUID),public.edge_inputs_ci_pack_update(UUID) TO :"edge_role";
RESET ROLE;
REVOKE CREATE ON SCHEMA public FROM aurum_edge_cash_owner;
COMMIT;
SQL

for relation in catalog lot pack; do
    case "$relation" in
        catalog) row_id=$retail_catalog_id ;;
        lot) row_id=$physical_lot_id ;;
        pack) row_id=$physical_pack_id ;;
    esac
    if denial="$(PGPASSWORD="$edge_password" psql -v ON_ERROR_STOP=1 -qAt \
        --host "$postgres_host" --port "$postgres_port" --username "$edge_role" \
        --dbname "$POSTGRES_DB" -v tenant_id="$tenant_id" -v branch_id="$branch_id" \
        -v edge_node_id="$edge_node_id" -v row_id="$row_id" \
        -v function_name="edge_inputs_ci_${relation}_update" 2>&1 <<'SQL'
\set VERBOSITY sqlstate
BEGIN;
SELECT set_config('app.tenant_id',:'tenant_id',true),
  set_config('app.branch_id',:'branch_id',true),set_config('app.edge_node_id',:'edge_node_id',true)
\gset
SELECT public.:"function_name"(:'row_id'::UUID);
COMMIT;
SQL
)"; then
        echo "Native inputs unexpectedly permitted $relation UPDATE" >&2
        exit 1
    fi
    case "$denial" in
        *42501*|*55000*) ;;
        *) echo "Native inputs UPDATE did not prove visible-row refusal: $relation" >&2; exit 1 ;;
    esac
done
psql_super <<'SQL'
DROP FUNCTION public.edge_inputs_ci_catalog_update(UUID);
DROP FUNCTION public.edge_inputs_ci_lot_update(UUID);
DROP FUNCTION public.edge_inputs_ci_pack_update(UUID);
SQL
test "$retail_before" = "$(retail_facts)"

# The node retains all resource locks until its outer transaction ends. A
# separate maintenance LOGIN must time out; no second node connection is used.
inputs_hash="$(retail_hash "$inputs_lock_payload")"
lock_application="aurum-input-lock-$edge_node_id"
PGAPPNAME="$lock_application" PGPASSWORD="$edge_password" psql -v ON_ERROR_STOP=1 -qAt \
    --host "$postgres_host" --port "$postgres_port" --username "$edge_role" \
    --dbname "$POSTGRES_DB" -v payload="$inputs_lock_payload" -v request_hash="$inputs_hash" \
    >/dev/null <<'SQL' &
BEGIN;
SELECT public.dispatch_edge_cash_sale_v1(
 (:'payload'::JSONB->'confirmation'->>'operation_id')::UUID,
 (:'payload'::JSONB->'scope'->>'activation_id')::UUID,
 (:'payload'::JSONB->'scope'->>'writer_epoch')::BIGINT,
 (:'payload'::JSONB->'scope'->>'user_id')::UUID,:'payload'::JSONB,:'request_hash')
\gset
SELECT pg_sleep(12);
ROLLBACK;
SQL
lock_holder_pid=$!
ready=false
attempt=0
while [ "$attempt" -lt 50 ]; do
    if [ "$(psql_super -v application="$lock_application" <<'SQL'
SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=:'application'
 AND wait_event='PgSleep' AND state='active');
SQL
)" = "t" ]; then
        ready=true
        break
    fi
    attempt=$((attempt + 1))
    sleep 0.1
done
if [ "$ready" != true ]; then
    wait "$lock_holder_pid" || true
    echo "Native inputs lock holder did not become ready" >&2
    exit 1
fi
psql_super -v catalog_id="$retail_catalog_id" -v lot_id="$physical_lot_id" \
    -v pack_id="$physical_pack_id" -v batch_id="$batch_id" <<'SQL'
BEGIN;
SET LOCAL lock_timeout='200ms';
CREATE TEMP TABLE locked_input_rows(relation REGCLASS,id UUID) ON COMMIT DROP;
INSERT INTO locked_input_rows VALUES ('public.tenant_catalog',:'catalog_id'::UUID),
 ('public.unit_stock_lot_v2',:'lot_id'::UUID),
 ('public.unit_stock_opened_pack_v2',:'pack_id'::UUID),('public.batch',:'batch_id'::UUID);
DO $locks$
DECLARE row RECORD; affected BIGINT;
BEGIN
 FOR row IN SELECT * FROM locked_input_rows LOOP
  BEGIN
   EXECUTE format('SELECT id FROM %s WHERE id=$1 FOR NO KEY UPDATE',row.relation) USING row.id;
   GET DIAGNOSTICS affected=ROW_COUNT;
   IF affected<>1 THEN RAISE EXCEPTION 'CI lock probe has no matching row'; END IF;
   RAISE EXCEPTION 'Native inputs did not retain a resource lock';
  EXCEPTION WHEN lock_not_available THEN NULL;
  END;
 END LOOP;
END $locks$;
ROLLBACK;
SQL
wait "$lock_holder_pid"
test "$retail_before" = "$(retail_facts)"

# Role attributes are checked on every preparation, not only at enrollment.
psql_super -v edge_role="$edge_role" <<'SQL'
ALTER ROLE :"edge_role" CONNECTION LIMIT 2;
SQL
if retail_call "$retail_payload" "$retail_request_hash" >/dev/null 2>&1; then
    echo "Native retail preparation accepted unsafe node role attributes" >&2
    exit 1
fi
psql_super -v edge_role="$edge_role" <<'SQL'
ALTER ROLE :"edge_role" CONNECTION LIMIT 1;
SQL
test "$retail_before" = "$(retail_facts)"

psql_super -v cashier_id="$cashier_id" <<'SQL'
UPDATE public.app_user SET status='blocked' WHERE id=:'cashier_id'::UUID;
SQL
blocked_cashier_facts="$(retail_facts)"
if retail_call "$retail_payload" "$retail_request_hash" >/dev/null 2>&1; then
    echo "Native retail preparation accepted a blocked cashier" >&2
    exit 1
fi
test "$blocked_cashier_facts" = "$(retail_facts)"
psql_super -v cashier_id="$cashier_id" <<'SQL'
UPDATE public.app_user SET status='active' WHERE id=:'cashier_id'::UUID;
SQL
retail_call "$retail_payload" "$retail_request_hash" >/dev/null

# An activated electronic plan blocks native cash; its immutable cancellation
# restores the existing draft behavior. Setup used the real cloud/app guards.
binding_cases=0
while IFS= read -r binding_fixture; do
    binding_mode="$(psql_super -v fixture="$binding_fixture" <<'SQL'
SELECT :'fixture'::JSONB->>'mode';
SQL
)"
    binding_payload="$(psql_super -v fixture="$binding_fixture" <<'SQL'
SELECT jsonb_set(:'fixture'::JSONB->'payload','{profile}','"retail_cash.draft.v1"'::JSONB);
SQL
)"
    binding_before="$(retail_facts)"
    case "$binding_mode" in
        active)
            if binding_denial="$(retail_call "$binding_payload" \
                "$(retail_hash "$binding_payload")" verbose 2>&1)"; then
                echo "Native cash accepted an active electronic payment binding" >&2
                exit 1
            fi
            case "$binding_denial" in
                *55000*'Native retail draft has active payment binding'*) ;;
                *) echo "Native binding refusal has an unexpected cause" >&2; exit 1 ;;
            esac
            ;;
        cancelled)
            binding_result="$(retail_call "$binding_payload" "$(retail_hash "$binding_payload")")"
            psql_super -v result="$binding_result" <<'SQL' >> "$draft_fixture_file"
SELECT jsonb_build_object('case','physical_cash:cancelled','expect','ok','draft',:'result'::JSONB);
SQL
            ;;
        *) echo "Unexpected native binding fixture" >&2; exit 1 ;;
    esac
    test "$binding_before" = "$(retail_facts)"
    binding_cases=$((binding_cases + 1))
done < /tmp/aurum-native-bindings.jsonl
test "$binding_cases" -eq 2

# Capture actual changed draft facts through a real native LOGIN. The later
# host-side pure check consumes only these redacted, fictional snapshots.
change_current_draft() {
    psql_super -v payload="$1" -v scenario="$2" <<'SQL'
BEGIN;
SET SESSION AUTHORIZATION aurum_app;
SELECT set_config('app.tenant_id',:'payload'::JSONB->'scope'->>'tenant_id',true),
  set_config('app.branch_id',:'payload'::JSONB->'scope'->>'branch_id',true),
  set_config('app.user_id',:'payload'::JSONB->'scope'->>'user_id',true),
  set_config('app.edge_node_id',:'payload'::JSONB->'scope'->>'edge_node_id',true)
\gset
SELECT id FROM public.sale
WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID FOR UPDATE
\gset
UPDATE public.pos_unit_cart_item
SET quantity=CASE :'scenario' WHEN 'quantity' THEN 2 ELSE 1 END,
 discount_amount=CASE :'scenario' WHEN 'discount' THEN 0.10 ELSE 0 END
WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND sale_id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID;
DELETE FROM public.prescription_log
WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND sale_id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID;
INSERT INTO public.prescription_log(tenant_id,sale_id,unit_cart_item_id,notes,created_by)
SELECT tenant_id,sale_id,CASE WHEN :'scenario'='rx_item' THEN id ELSE NULL END,
 CASE WHEN :'scenario'='rx_blank' THEN E' \t\n' ELSE 'CI reference' END,
 (:'payload'::JSONB->'scope'->>'user_id')::UUID
FROM public.pos_unit_cart_item
WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND sale_id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID
 AND :'scenario' IN ('rx_blank','rx_global','rx_item');
RESET SESSION AUTHORIZATION;
-- The maintenance identity changes only fictional fixture catalog settings.
-- Every table guard remains enabled.
UPDATE public.tenant_catalog SET
 dispensing_type=CASE WHEN :'scenario' LIKE 'rx_%' THEN 'prescription' ELSE 'otc' END,
 unit_sale_preparation=jsonb_set(unit_sale_preparation,'{unit_price}',
   to_jsonb(CASE :'scenario' WHEN 'price' THEN '1.50' ELSE '1.00' END))
WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND id IN (SELECT catalog_id FROM public.pos_unit_cart_item
   WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
     AND sale_id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID);
COMMIT;
SQL
}
verify_current_draft_resources() {
    psql_super -v edge_role="$edge_role" <<'SQL'
BEGIN;
GRANT CREATE ON SCHEMA public TO aurum_edge_cash_owner;
SET LOCAL ROLE aurum_edge_cash_owner;
CREATE FUNCTION public.edge_draft_ci_unit_update(p_id UUID) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
BEGIN
 PERFORM 1 FROM public.pos_unit_cart_item WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'CI draft row not visible'; END IF;
 BEGIN
  UPDATE public.pos_unit_cart_item SET id=id WHERE id=p_id;
 EXCEPTION WHEN insufficient_privilege OR object_not_in_prerequisite_state THEN RETURN true;
 END;
 RAISE EXCEPTION 'CI draft write unexpectedly succeeded';
END $$;
CREATE FUNCTION public.edge_draft_ci_rx_update(p_id UUID) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET row_security=on AS $$
BEGIN
 PERFORM 1 FROM public.prescription_log WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'CI Rx row not visible'; END IF;
 BEGIN
  UPDATE public.prescription_log SET id=id WHERE id=p_id;
 EXCEPTION WHEN insufficient_privilege OR object_not_in_prerequisite_state THEN RETURN true;
 END;
 RAISE EXCEPTION 'CI Rx write unexpectedly succeeded';
END $$;
REVOKE ALL ON FUNCTION public.edge_draft_ci_unit_update(UUID),
 public.edge_draft_ci_rx_update(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.edge_draft_ci_unit_update(UUID),
 public.edge_draft_ci_rx_update(UUID) TO :"edge_role";
RESET ROLE;
REVOKE CREATE ON SCHEMA public FROM aurum_edge_cash_owner;
COMMIT;
SQL
    for draft_relation in unit rx; do
        draft_row_id="$(psql_super -v payload="$1" -v relation="$draft_relation" <<'SQL'
SELECT id FROM public.pos_unit_cart_item
WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND sale_id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID AND :'relation'='unit'
UNION ALL SELECT id FROM public.prescription_log
WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND sale_id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID AND :'relation'='rx';
SQL
)"
        test "$(PGPASSWORD="$edge_password" psql -v ON_ERROR_STOP=1 -qAt \
            --host "$postgres_host" --port "$postgres_port" --username "$edge_role" \
            --dbname "$POSTGRES_DB" -v row_id="$draft_row_id" \
            -v function_name="edge_draft_ci_${draft_relation}_update" <<'SQL'
SELECT public.:"function_name"(:'row_id'::UUID);
SQL
)" = "t"
    done
    psql_super <<'SQL'
DROP FUNCTION public.edge_draft_ci_unit_update(UUID);
DROP FUNCTION public.edge_draft_ci_rx_update(UUID);
SQL
    PGAPPNAME=aurum-current-draft-lock PGPASSWORD="$edge_password" \
        psql -v ON_ERROR_STOP=1 -qAt --host "$postgres_host" --port "$postgres_port" \
        --username "$edge_role" --dbname "$POSTGRES_DB" \
        -v payload="$1" -v request_hash="$(retail_hash "$1")" <<'SQL' >/dev/null &
BEGIN;
SELECT public.dispatch_edge_cash_sale_v1(
 (:'payload'::JSONB->'confirmation'->>'operation_id')::UUID,
 (:'payload'::JSONB->'scope'->>'activation_id')::UUID,
 (:'payload'::JSONB->'scope'->>'writer_epoch')::BIGINT,
 (:'payload'::JSONB->'scope'->>'user_id')::UUID,:'payload'::JSONB,:'request_hash')
\gset
SELECT pg_sleep(12);
ROLLBACK;
SQL
    draft_lock_pid=$!
    draft_ready=false
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        if [ "$(psql_super -v edge_role="$edge_role" <<'SQL'
SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usename=:'edge_role'
 AND application_name='aurum-current-draft-lock' AND wait_event='PgSleep');
SQL
)" = "t" ]; then
            draft_ready=true
            break
        fi
        sleep 0.2
    done
    if [ "$draft_ready" != true ]; then
        wait "$draft_lock_pid" || true
        echo "Current draft lock holder did not become ready" >&2
        exit 1
    fi
    psql_super -v payload="$1" <<'SQL'
BEGIN;
SET LOCAL lock_timeout='200ms';
CREATE TEMP TABLE current_draft_lock_rows(relation REGCLASS,id UUID) ON COMMIT DROP;
INSERT INTO current_draft_lock_rows
SELECT 'public.pos_unit_cart_item'::REGCLASS,id FROM public.pos_unit_cart_item
 WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND sale_id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID
UNION ALL SELECT 'public.prescription_log'::REGCLASS,id FROM public.prescription_log
 WHERE tenant_id=(:'payload'::JSONB->'scope'->>'tenant_id')::UUID
 AND sale_id=(:'payload'::JSONB->'confirmation'->>'sale_id')::UUID;
DO $locks$
DECLARE row RECORD; affected BIGINT;
BEGIN
 IF (SELECT count(*) FROM current_draft_lock_rows)<>2 THEN
  RAISE EXCEPTION 'CI draft lock rows missing'; END IF;
 FOR row IN SELECT * FROM current_draft_lock_rows LOOP
  BEGIN
   EXECUTE format('SELECT id FROM %s WHERE id=$1 FOR NO KEY UPDATE',row.relation) USING row.id;
   GET DIAGNOSTICS affected=ROW_COUNT;
   IF affected<>1 THEN RAISE EXCEPTION 'CI draft lock row not visible'; END IF;
   RAISE EXCEPTION 'Native current draft did not retain a lock';
  EXCEPTION WHEN lock_not_available THEN NULL;
  END;
 END LOOP;
END $locks$;
ROLLBACK;
SQL
    wait "$draft_lock_pid"
}
old_ifs=$IFS
IFS='
'
for retail_payload in $retail_payloads; do
    draft_payload="$(psql_super -v payload="$retail_payload" <<'SQL'
SELECT jsonb_set(:'payload'::JSONB,'{profile}','"retail_cash.draft.v1"'::JSONB);
SQL
)"
    draft_hash="$(retail_hash "$draft_payload")"
    for draft_scenario in quantity discount price rx_missing rx_blank rx_global rx_item restored; do
        change_current_draft "$retail_payload" "$draft_scenario"
        current_before="$(retail_facts)"
        draft_result="$(retail_call "$draft_payload" "$draft_hash")"
        if [ "$draft_scenario" = rx_item ] && [ "$(psql_super -v payload="$retail_payload" <<'SQL'
SELECT :'payload'::JSONB->>'kind';
SQL
)" = physical_cash ]; then
            verify_current_draft_resources "$draft_payload"
        fi
        test "$current_before" = "$(retail_facts)"
        psql_super -v result="$draft_result" -v payload="$retail_payload" \
            -v scenario="$draft_scenario" <<'SQL' >> "$draft_fixture_file"
SELECT jsonb_build_object('case',(:'payload'::JSONB->>'kind')||':'||:'scenario',
 'expect',CASE WHEN :'scenario' IN ('quantity','discount','price') THEN 'conflict'
   WHEN :'scenario' IN ('rx_missing','rx_blank') THEN 'business' ELSE 'ok' END,
 'draft',:'result'::JSONB);
SQL
    done
done
IFS=$old_ifs

test "$(psql_super -v edge_role="$edge_role" <<'SQL'
SELECT NOT has_table_privilege(:'edge_role','public.pos_unit_cash_intent','SELECT')
  AND NOT has_any_column_privilege(:'edge_role','public.pos_unit_cash_intent','SELECT')
  AND NOT has_function_privilege(:'edge_role',
    'public.prepare_edge_retail_cash_v1(uuid,uuid,bigint,uuid,jsonb,text)','EXECUTE')
  AND NOT has_function_privilege('aurum_app',
    'public.dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)','EXECUTE')
  AND NOT has_function_privilege('aurum_support',
    'public.dispatch_edge_cash_sale_v1(uuid,uuid,bigint,uuid,jsonb,text)','EXECUTE')
  AND NOT has_any_column_privilege(:'edge_role','public.unit_stock_lot_v2','SELECT,INSERT,UPDATE')
  AND NOT has_any_column_privilege(:'edge_role','public.unit_stock_opened_pack_v2','SELECT,INSERT,UPDATE')
  AND NOT has_function_privilege(:'edge_role',
    'public.lock_edge_retail_cash_inputs_v1(uuid,uuid,bigint,uuid,jsonb,text)','EXECUTE');
SQL
)" = "t"

# A genuine per-node LOGIN inherits the executor group but must still be
# denied direct access to the private helper before any envelope validation.
if denial="$(PGPASSWORD="$edge_password" psql -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -qAt \
    --host "$postgres_host" --port "$postgres_port" --username "$edge_role" \
    --dbname "$POSTGRES_DB" -c "SELECT public.lock_edge_retail_cash_inputs_v1( \
      NULL::UUID,NULL::UUID,1,NULL::UUID,'{}'::JSONB,repeat('a',64))" 2>&1)"; then
    echo "Native node unexpectedly invoked the private inputs helper" >&2
    exit 1
fi
case "$denial" in
    *42501*) ;;
    *) echo "Native helper refusal did not prove an access denial" >&2; exit 1 ;;
esac

if denial="$(PGPASSWORD="$edge_password" psql -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -qAt \
    --host "$postgres_host" --port "$postgres_port" --username "$edge_role" \
    --dbname "$POSTGRES_DB" -c "SELECT public.lock_edge_retail_cash_draft_v1( \
      NULL::UUID,NULL::UUID,1,NULL::UUID,'{}'::JSONB,repeat('a',64))" 2>&1)"; then
    echo "Native node unexpectedly invoked the private current-draft helper" >&2
    exit 1
fi
case "$denial" in
    *42501*) ;;
    *) echo "Native current-draft helper did not prove an access denial" >&2; exit 1 ;;
esac

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
if retail_call "$retail_payload" "$retail_request_hash" >/dev/null 2>&1; then
    echo "Native retail preparation accepted a revoked Edge node" >&2
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
