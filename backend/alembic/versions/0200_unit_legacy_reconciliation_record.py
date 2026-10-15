"""Immutable records of legacy physical counts, without changing inventory.

Revision ID: 0200
Revises: 0199
"""

import re

from alembic import op

revision = "0200"
down_revision = "0199"
branch_labels = None
depends_on = None

COUNT_VALIDATOR = "public.validate_unit_legacy_reconciliation_count(JSONB)"
RECORD_GUARD = "public.trg_unit_legacy_reconciliation_record_guard()"


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.get_bind().exec_driver_sql(statement)
            start = match.end()
    if sql[start:].strip():
        op.get_bind().exec_driver_sql(sql[start:])


COUNT_SQL = r"""
CREATE FUNCTION public.validate_unit_legacy_reconciliation_count(p_count JSONB)
RETURNS VOID LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_packaging JSONB; v_pack JSONB; v_state JSONB; v_units BIGINT;
 v_closed BIGINT; v_size INTEGER; v_step INTEGER; v_blister INTEGER; v_id UUID;
 v_seen UUID[]:=ARRAY[]::UUID[]; v_remainder BIGINT;
BEGIN
 IF p_count IS NULL OR jsonb_typeof(p_count) IS DISTINCT FROM 'object'
   OR NOT p_count ?& ARRAY['packaging','closed_packs','opened_packs']
   OR p_count-ARRAY['packaging','closed_packs','opened_packs']<>'{}'::JSONB
   OR jsonb_typeof(p_count->'closed_packs') IS DISTINCT FROM 'number'
   OR NOT (p_count->>'closed_packs' ~ '^[0-9]{1,11}$')
   OR jsonb_typeof(p_count->'opened_packs') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'legacy_reconciliation_count_shape' USING ERRCODE='23514';
 END IF;
 v_packaging:=p_count->'packaging';
 IF jsonb_typeof(v_packaging) IS DISTINCT FROM 'object'
   OR NOT v_packaging ?& ARRAY[
     'version','unit','units_per_pack','units_per_blister','dispense_step']
   OR v_packaging-ARRAY[
     'version','unit','units_per_pack','units_per_blister','dispense_step']<>'{}'::JSONB
   OR jsonb_typeof(v_packaging->'version') IS DISTINCT FROM 'number'
   OR v_packaging->>'version' IS DISTINCT FROM '2'
   OR jsonb_typeof(v_packaging->'unit') IS DISTINCT FROM 'string'
   OR v_packaging->>'unit' NOT IN ('tablet','capsule','ampoule','sachet','suppository')
   OR jsonb_typeof(v_packaging->'units_per_pack') IS DISTINCT FROM 'number'
   OR NOT (v_packaging->>'units_per_pack' ~ '^[0-9]{1,5}$')
   OR jsonb_typeof(v_packaging->'dispense_step') IS DISTINCT FROM 'number'
   OR NOT (v_packaging->>'dispense_step' ~ '^[0-9]{1,5}$') THEN
  RAISE EXCEPTION 'legacy_reconciliation_packaging_shape' USING ERRCODE='23514';
 END IF;
 v_size:=(v_packaging->>'units_per_pack')::INTEGER;
 v_step:=(v_packaging->>'dispense_step')::INTEGER;
 IF v_size NOT BETWEEN 2 AND 10000 OR v_step NOT BETWEEN 1 AND 10000 THEN
  RAISE EXCEPTION 'legacy_reconciliation_packaging_range' USING ERRCODE='23514';
 END IF;
 IF v_size%v_step<>0 THEN
  RAISE EXCEPTION 'legacy_reconciliation_packaging_step' USING ERRCODE='23514';
 END IF;
 IF v_packaging->'units_per_blister'<>'null'::JSONB THEN
  IF jsonb_typeof(v_packaging->'units_per_blister') IS DISTINCT FROM 'number'
    OR NOT (v_packaging->>'units_per_blister' ~ '^[0-9]{1,5}$') THEN
   RAISE EXCEPTION 'legacy_reconciliation_blister_shape' USING ERRCODE='23514';
  END IF;
  v_blister:=(v_packaging->>'units_per_blister')::INTEGER;
  IF v_blister NOT BETWEEN 2 AND 10000 THEN
   RAISE EXCEPTION 'legacy_reconciliation_blister_range' USING ERRCODE='23514';
  END IF;
  IF v_size%v_blister<>0 THEN
   RAISE EXCEPTION 'legacy_reconciliation_blister_composition' USING ERRCODE='23514';
  END IF;
 END IF;
 v_closed:=(p_count->>'closed_packs')::BIGINT;
 v_units:=v_closed*v_size;
 IF v_units>99999999999 THEN
  RAISE EXCEPTION 'legacy_reconciliation_count_limit' USING ERRCODE='23514';
 END IF;
 FOR v_pack IN SELECT value FROM jsonb_array_elements(p_count->'opened_packs') LOOP
  IF jsonb_typeof(v_pack) IS DISTINCT FROM 'object'
    OR NOT v_pack ?& ARRAY['id','opened_at','intact_blisters','opened_blisters','loose_units']
    OR v_pack-ARRAY['id','opened_at','intact_blisters','opened_blisters','loose_units']
      <>'{}'::JSONB
    OR jsonb_typeof(v_pack->'id') IS DISTINCT FROM 'string'
    OR jsonb_typeof(v_pack->'opened_at') IS DISTINCT FROM 'string'
    OR NOT (v_pack->>'opened_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$')
    OR jsonb_typeof(v_pack->'intact_blisters') IS DISTINCT FROM 'array'
    OR jsonb_typeof(v_pack->'opened_blisters') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'legacy_reconciliation_opened_pack_shape' USING ERRCODE='23514';
  END IF;
  v_id:=(v_pack->>'id')::UUID;
  PERFORM (v_pack->>'opened_at')::TIMESTAMPTZ;
  IF v_id=ANY(v_seen) THEN
   RAISE EXCEPTION 'legacy_reconciliation_pack_duplicate' USING ERRCODE='23514';
  END IF;
  v_seen:=array_append(v_seen,v_id);
  -- The ledger's private structural validator requires sorted blister arrays.
  -- Sort without deduplicating; duplicate identities still fail its checks.
  v_state:=jsonb_build_object(
    'intact_blisters',coalesce((SELECT jsonb_agg(value ORDER BY value)
      FROM jsonb_array_elements(v_pack->'intact_blisters')),'[]'::JSONB),
    'opened_blisters',coalesce((SELECT jsonb_agg(value ORDER BY value->'number')
      FROM jsonb_array_elements(v_pack->'opened_blisters')),'[]'::JSONB),
    'loose_units',v_pack->'loose_units');
  v_remainder:=public.unit_pack_v2_remaining(v_state,v_packaging);
  IF v_remainder<=0 THEN
   RAISE EXCEPTION 'legacy_reconciliation_empty_opened_pack' USING ERRCODE='23514';
  END IF;
  v_units:=v_units+v_remainder;
  IF v_units>99999999999 THEN
   RAISE EXCEPTION 'legacy_reconciliation_count_limit' USING ERRCODE='23514';
  END IF;
 END LOOP;
EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format
  OR datetime_field_overflow OR numeric_value_out_of_range THEN
 RAISE EXCEPTION 'legacy_reconciliation_count_invalid' USING ERRCODE='23514';
END $$
"""

GUARD_SQL = """
CREATE FUNCTION public.trg_unit_legacy_reconciliation_record_guard()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_source RECORD; v_policy public.tenant_access_policy%ROWTYPE; v_status TEXT;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'legacy_reconciliation_record_immutable' USING ERRCODE='55000';
 END IF;
 IF session_user<>'aurum_app' OR NEW.tenant_id IS NULL OR NEW.id IS NULL
   OR NEW.branch_id IS NULL OR NEW.batch_id IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR coalesce(public.is_support_session(),true)
   OR coalesce(public.is_tenant_support_session(),true)
   OR NOT EXISTS(SELECT 1 FROM public.app_user
       WHERE id=public.current_app_user_id() AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
       NEW.tenant_id,'batches.view',NEW.branch_id),false)
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
       NEW.tenant_id,'batches.update',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'legacy_reconciliation_actor_denied' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'legacy_reconciliation_isolation_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('tenant-access:'||NEW.tenant_id,0));
 SELECT status INTO v_status FROM public.tenant WHERE id=NEW.tenant_id;
 SELECT * INTO v_policy FROM public.tenant_access_policy WHERE tenant_id=NEW.tenant_id;
 IF NOT FOUND OR v_status IN ('readonly','suspended','archived') OR (
   (v_policy.expires_at IS NULL OR v_policy.expires_at>statement_timestamp())
   AND (v_policy.mode='readonly' OR 'inventory'=ANY(v_policy.blocked_modules))) THEN
  RAISE EXCEPTION 'legacy_reconciliation_access_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'aurum-legacy-reconciliation-key:'||NEW.tenant_id||':'||NEW.id,0));
 SELECT catalog_id,qty_remaining,unit_packaging_preparation,unit_packaging_snapshot_v2
   INTO v_source FROM public.batch
   WHERE tenant_id=NEW.tenant_id AND branch_id=NEW.branch_id AND id=NEW.batch_id
   FOR SHARE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'legacy_reconciliation_source_missing' USING ERRCODE='P0002';
 END IF;
 IF v_source.unit_packaging_preparation IS NOT NULL
   OR v_source.unit_packaging_snapshot_v2 IS NOT NULL
   OR NEW.legacy_quantity IS DISTINCT FROM v_source.qty_remaining THEN
  RAISE EXCEPTION 'legacy_reconciliation_source_changed' USING ERRCODE='23505';
 END IF;
 PERFORM public.validate_unit_legacy_reconciliation_count(NEW.count);
 NEW.catalog_id:=v_source.catalog_id;
 NEW.legacy_quantity:=v_source.qty_remaining;
 NEW.created_by:=public.current_app_user_id();
 NEW.created_at:=statement_timestamp();
 RETURN NEW;
END $$
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE legacy_reconciliation_reference_grants AS
        SELECT oid::regclass AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.batch'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.batch,public.app_user TO aurum_schema_owner;
      CREATE TABLE public.unit_legacy_reconciliation_record (
        tenant_id UUID NOT NULL,id UUID NOT NULL,branch_id UUID NOT NULL,
        batch_id UUID NOT NULL,catalog_id UUID NOT NULL,
        legacy_quantity NUMERIC(14,3) NOT NULL CHECK(legacy_quantity>=0),
        count JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL,created_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,batch_id) REFERENCES public.batch(tenant_id,id),
        FOREIGN KEY(created_by) REFERENCES public.app_user(id)
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.legacy_reconciliation_reference_grants
          WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.legacy_reconciliation_reference_grants;
      CREATE INDEX ix_unit_legacy_reconciliation_batch
        ON public.unit_legacy_reconciliation_record(tenant_id,branch_id,batch_id,created_at);
      ALTER TABLE public.unit_legacy_reconciliation_record OWNER TO aurum_schema_owner;
      ALTER TABLE public.unit_legacy_reconciliation_record ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.unit_legacy_reconciliation_record FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.unit_legacy_reconciliation_record FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.unit_legacy_reconciliation_record TO aurum_app;
      GRANT SELECT ON public.unit_legacy_reconciliation_record TO aurum_support;
      CREATE POLICY schema_owner ON public.unit_legacy_reconciliation_record
        TO aurum_schema_owner USING(true) WITH CHECK(true);
      CREATE POLICY support_read ON public.unit_legacy_reconciliation_record
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_read ON public.unit_legacy_reconciliation_record
        FOR SELECT TO aurum_app USING(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id));
      CREATE POLICY tenant_insert ON public.unit_legacy_reconciliation_record
        FOR INSERT TO aurum_app WITH CHECK(tenant_id=public.current_tenant_id() AND
          public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id) AND
          public.tenant_actor_has_scoped_permission(tenant_id,'batches.update',branch_id));
    """)
    _sql(COUNT_SQL)
    _sql(GUARD_SQL)
    for signature in (COUNT_VALIDATOR, RECORD_GUARD):
        op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,aurum_app,aurum_support")
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.unit_legacy_reconciliation_record FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
      CREATE TRIGGER a10_unit_legacy_reconciliation_record_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.unit_legacy_reconciliation_record FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_legacy_reconciliation_record_guard();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.unit_legacy_reconciliation_record FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.unit_legacy_reconciliation_record IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_legacy_reconciliation_record) THEN
          RAISE EXCEPTION 'legacy_reconciliation_history_exists' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TABLE public.unit_legacy_reconciliation_record;
    """)
    for signature in (RECORD_GUARD, COUNT_VALIDATOR):
        op.execute(f"DROP FUNCTION {signature}")
