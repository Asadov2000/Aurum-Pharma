"""Independent prepared integer stock; no legacy stock conversion or activation.

Revision ID: 0162
Revises: 0161
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0162"
down_revision: str | Sequence[str] | None = "0161"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("unit_stock_operation", "unit_stock_lot", "unit_stock_opened_pack", "unit_stock_entry")
IDENTITY = """
 id UUID PRIMARY KEY,
 tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE RESTRICT,
 branch_id UUID NOT NULL,
 catalog_id UUID NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
 updated_by UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
 FOREIGN KEY(tenant_id,branch_id) REFERENCES public.branch(tenant_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(tenant_id,catalog_id) REFERENCES public.tenant_catalog(tenant_id,id) ON DELETE RESTRICT
"""
PACKAGING = """
 unit TEXT NOT NULL CHECK(unit IN ('tablet','capsule','ampoule','sachet','suppository')),
 units_per_pack INTEGER NOT NULL CHECK(units_per_pack BETWEEN 2 AND 10000),
 dispense_step INTEGER NOT NULL CHECK(dispense_step BETWEEN 1 AND 10000),
 CHECK(units_per_pack % dispense_step = 0)
"""


def _sql(sql: str) -> None:
    """asyncpg prepares a single statement at a time."""
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.execute(statement)
            start = match.end()
    if sql[start:].strip():
        op.execute(sql[start:])


GUARD_SQL = r"""
CREATE FUNCTION public.trg_unit_stock_guard() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_lot public.unit_stock_lot%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'unit_stock_immutable' USING ERRCODE='55000';
 END IF;
 IF TG_OP='UPDATE' THEN
   IF TG_TABLE_NAME NOT IN ('unit_stock_lot','unit_stock_opened_pack')
      OR current_user<>'aurum_schema_owner' OR pg_trigger_depth()<>2 THEN
     RAISE EXCEPTION 'unit_stock_immutable' USING ERRCODE='55000';
   END IF;
   IF TG_TABLE_NAME='unit_stock_lot' THEN
     IF to_jsonb(NEW)-ARRAY['closed_packs','updated_at','updated_by']
       <>to_jsonb(OLD)-ARRAY['closed_packs','updated_at','updated_by'] THEN
       RAISE EXCEPTION 'unit_stock_identity_immutable' USING ERRCODE='55000';
     END IF;
   ELSIF to_jsonb(NEW)-ARRAY['remaining_units','updated_at','updated_by']
       <>to_jsonb(OLD)-ARRAY['remaining_units','updated_at','updated_by'] THEN
     RAISE EXCEPTION 'unit_stock_identity_immutable' USING ERRCODE='55000';
   END IF;
   NEW.updated_at:=statement_timestamp();
   NEW.updated_by:=public.current_app_user_id();
   RETURN NEW;
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
 NEW.created_by:=public.current_app_user_id(); NEW.updated_by:=NEW.created_by;
 IF TG_TABLE_NAME='unit_stock_lot' THEN
   IF NEW.closed_packs<>0 THEN
     RAISE EXCEPTION 'unit_stock_projection_requires_entry' USING ERRCODE='23514';
   END IF;
 ELSIF TG_TABLE_NAME='unit_stock_opened_pack' THEN
   IF NEW.remaining_units<>0 THEN
     RAISE EXCEPTION 'unit_stock_projection_requires_entry' USING ERRCODE='23514';
   END IF;
   SELECT * INTO v_lot FROM public.unit_stock_lot
     WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
   IF NOT FOUND OR v_lot.branch_id<>NEW.branch_id OR v_lot.catalog_id<>NEW.catalog_id THEN
     RAISE EXCEPTION 'unit_stock_pack_scope' USING ERRCODE='23514';
   END IF;
   NEW.opened_at:=statement_timestamp();
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_stock_entry_apply() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_op public.unit_stock_operation%ROWTYPE;
 v_lot public.unit_stock_lot%ROWTYPE;
 v_pack public.unit_stock_opened_pack%ROWTYPE;
 v_closed NUMERIC; v_remaining NUMERIC;
BEGIN
 -- Command identity is claimed first; balances are serialized catalog -> lot -> pack.
 PERFORM 1 FROM public.tenant_catalog
   WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id FOR NO KEY UPDATE;
 SELECT * INTO v_op FROM public.unit_stock_operation
   WHERE tenant_id=NEW.tenant_id AND id=NEW.operation_id;
 SELECT * INTO v_lot FROM public.unit_stock_lot
   WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id FOR UPDATE;
 IF v_op.id IS NULL OR v_lot.id IS NULL
   OR v_op.branch_id<>NEW.branch_id OR v_op.catalog_id<>NEW.catalog_id
   OR v_lot.branch_id<>NEW.branch_id OR v_lot.catalog_id<>NEW.catalog_id
   OR (v_op.unit,v_op.units_per_pack,v_op.dispense_step)
     IS DISTINCT FROM (v_lot.unit,v_lot.units_per_pack,v_lot.dispense_step) THEN
   RAISE EXCEPTION 'unit_stock_entry_scope_snapshot' USING ERRCODE='23514';
 END IF;
 IF NEW.pack_id IS NOT NULL THEN
   SELECT * INTO v_pack FROM public.unit_stock_opened_pack
     WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id AND lot_id=NEW.lot_id FOR UPDATE;
   IF NOT FOUND OR v_pack.branch_id<>NEW.branch_id OR v_pack.catalog_id<>NEW.catalog_id THEN
     RAISE EXCEPTION 'unit_stock_entry_pack_scope' USING ERRCODE='23514';
   END IF;
 END IF;
 IF v_op.kind='initialize' THEN
   IF NEW.kind<>'initialize' OR v_op.sale_format<>'pack'
      OR EXISTS(SELECT 1 FROM public.unit_stock_entry e
        WHERE e.tenant_id=NEW.tenant_id AND e.lot_id=NEW.lot_id
          AND e.kind='initialize' AND e.id<>NEW.id) THEN
     RAISE EXCEPTION 'unit_stock_initialization_once' USING ERRCODE='23514';
   END IF;
 ELSE
   -- Expiry is checked against the explicit pharmacy-local date by the service.
   IF v_lot.is_blocked OR NEW.kind='initialize'
      OR NOT EXISTS(SELECT 1 FROM public.unit_stock_entry e
        WHERE e.tenant_id=NEW.tenant_id AND e.lot_id=NEW.lot_id AND e.kind='initialize')
      OR (v_op.sale_format='pack' AND NEW.kind<>'pack_take')
      OR (v_op.sale_format='unit' AND NEW.kind NOT IN ('unit_take','unit_open')) THEN
     RAISE EXCEPTION 'unit_stock_dispense_invalid' USING ERRCODE='23514';
   END IF;
 END IF;
 IF NEW.kind IN ('initialize','pack_take') THEN
   IF NEW.units_delta::NUMERIC<>NEW.closed_delta::NUMERIC*v_lot.units_per_pack THEN
     RAISE EXCEPTION 'unit_stock_closed_arithmetic' USING ERRCODE='23514';
   END IF;
 ELSIF NEW.kind='unit_take' THEN
   IF (-NEW.units_delta::NUMERIC)%v_lot.dispense_step<>0
      OR NEW.pack_remaining_after::NUMERIC<>v_pack.remaining_units::NUMERIC+NEW.opened_delta THEN
     RAISE EXCEPTION 'unit_stock_take_arithmetic' USING ERRCODE='23514';
   END IF;
 ELSE
   IF (-NEW.units_delta::NUMERIC)%v_lot.dispense_step<>0
     OR -NEW.closed_delta::NUMERIC<>ceil(-NEW.units_delta::NUMERIC/v_lot.units_per_pack)
     OR NEW.opened_delta::NUMERIC<>(-NEW.closed_delta::NUMERIC)*v_lot.units_per_pack+NEW.units_delta
     OR NEW.opened_delta>=v_lot.units_per_pack
     OR (NEW.opened_delta>0 AND (NEW.pack_id IS NULL OR v_pack.remaining_units<>0
       OR EXISTS(SELECT 1 FROM public.unit_stock_entry e
          WHERE e.tenant_id=NEW.tenant_id AND e.pack_id=NEW.pack_id AND e.id<>NEW.id)))
     OR (NEW.opened_delta=0 AND NEW.pack_id IS NOT NULL) THEN
     RAISE EXCEPTION 'unit_stock_open_arithmetic' USING ERRCODE='23514';
   END IF;
 END IF;
 v_closed:=v_lot.closed_packs::NUMERIC+NEW.closed_delta;
 IF v_closed<0 OR v_closed*v_lot.units_per_pack>99999999999 THEN
   RAISE EXCEPTION 'unit_stock_insufficient_closed' USING ERRCODE='23514';
 END IF;
 UPDATE public.unit_stock_lot SET closed_packs=v_closed::BIGINT
   WHERE tenant_id=NEW.tenant_id AND id=NEW.lot_id;
 IF NEW.pack_id IS NOT NULL THEN
   v_remaining:=v_pack.remaining_units::NUMERIC+NEW.opened_delta;
   IF v_remaining<0 OR v_remaining>=v_lot.units_per_pack
      OR v_remaining<>NEW.pack_remaining_after THEN
     RAISE EXCEPTION 'unit_stock_insufficient_opened' USING ERRCODE='23514';
   END IF;
   UPDATE public.unit_stock_opened_pack SET remaining_units=v_remaining::BIGINT
     WHERE tenant_id=NEW.tenant_id AND id=NEW.pack_id;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_unit_stock_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_op public.unit_stock_operation%ROWTYPE;
 v_id UUID; v_sum NUMERIC; v_count BIGINT; v_expected NUMERIC;
BEGIN
 IF TG_TABLE_NAME='unit_stock_lot' THEN
   IF NOT EXISTS(SELECT 1 FROM public.unit_stock_entry e
     WHERE e.tenant_id=NEW.tenant_id AND e.lot_id=NEW.id AND e.kind='initialize') THEN
     RAISE EXCEPTION 'unit_stock_orphan_lot' USING ERRCODE='23514';
   END IF;
   RETURN NULL;
 ELSIF TG_TABLE_NAME='unit_stock_opened_pack' THEN
   IF NOT EXISTS(SELECT 1 FROM public.unit_stock_entry e
     WHERE e.tenant_id=NEW.tenant_id AND e.pack_id=NEW.id AND e.kind='unit_open') THEN
     RAISE EXCEPTION 'unit_stock_orphan_pack' USING ERRCODE='23514';
   END IF;
   RETURN NULL;
 END IF;
 IF TG_TABLE_NAME='unit_stock_operation' THEN v_id:=NEW.id;
 ELSE v_id:=NEW.operation_id; END IF;
 SELECT * INTO v_op FROM public.unit_stock_operation WHERE tenant_id=NEW.tenant_id AND id=v_id;
 SELECT count(*),coalesce(sum(units_delta),0) INTO v_count,v_sum
   FROM public.unit_stock_entry WHERE tenant_id=NEW.tenant_id AND operation_id=v_id;
 IF v_op.kind='initialize' THEN v_expected:=v_op.base_units::NUMERIC;
 ELSE v_expected:=-v_op.base_units::NUMERIC; END IF;
 IF v_count=0 OR v_sum<>v_expected
    OR (v_op.kind='initialize' AND v_count<>1) THEN
   RAISE EXCEPTION 'unit_stock_operation_incomplete' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
"""


def upgrade() -> None:
    # Preserve the migrator's reference ACLs exactly, including older installs.
    _sql("""
      CREATE TEMP TABLE unit_stock_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.tenant'::regclass,'public.branch'::regclass,
          'public.tenant_catalog'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.tenant,public.branch,public.tenant_catalog,public.app_user
        TO aurum_schema_owner;
    """)
    op.execute(f"""
      CREATE TABLE public.unit_stock_operation (
        {IDENTITY.replace('id UUID PRIMARY KEY', 'id UUID NOT NULL')}, {PACKAGING},
        kind TEXT NOT NULL CHECK(kind IN ('initialize','dispense')),
        sale_format TEXT NOT NULL CHECK(sale_format IN ('pack','unit')),
        quantity BIGINT NOT NULL CHECK(quantity>0 AND quantity<=99999999999),
        base_units BIGINT NOT NULL CHECK(base_units>0 AND base_units<=99999999999),
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{{64}}$'),
        CHECK((sale_format='pack' AND base_units::NUMERIC=quantity::NUMERIC*units_per_pack)
          OR (sale_format='unit' AND base_units=quantity AND quantity%dispense_step=0)),
        CHECK(kind<>'initialize' OR sale_format='pack'),
        PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,id,branch_id,catalog_id)
      )
    """)
    op.execute(f"""
      CREATE TABLE public.unit_stock_lot (
        {IDENTITY}, {PACKAGING},
        state TEXT NOT NULL DEFAULT 'prepared' CHECK(state='prepared'),
        version INTEGER NOT NULL DEFAULT 1 CHECK(version=1),
        expires_at DATE NOT NULL,
        is_blocked BOOLEAN NOT NULL DEFAULT false CHECK(NOT is_blocked),
        closed_packs BIGINT NOT NULL DEFAULT 0 CHECK(closed_packs>=0),
        CHECK(closed_packs::NUMERIC*units_per_pack<=99999999999),
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,id,branch_id,catalog_id)
      )
    """)
    op.execute(f"""
      CREATE TABLE public.unit_stock_opened_pack (
        {IDENTITY}, lot_id UUID NOT NULL,
        opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        remaining_units BIGINT NOT NULL DEFAULT 0 CHECK(remaining_units>=0),
        UNIQUE(tenant_id,id,lot_id), UNIQUE(tenant_id,id,lot_id,branch_id,catalog_id),
        FOREIGN KEY(tenant_id,lot_id,branch_id,catalog_id)
          REFERENCES public.unit_stock_lot(tenant_id,id,branch_id,catalog_id) ON DELETE RESTRICT
      )
    """)
    op.execute(f"""
      CREATE TABLE public.unit_stock_entry (
        {IDENTITY}, operation_id UUID NOT NULL, lot_id UUID NOT NULL, pack_id UUID,
        kind TEXT NOT NULL CHECK(kind IN ('initialize','pack_take','unit_take','unit_open')),
        entry_order INTEGER NOT NULL CHECK(entry_order>=0),
        closed_delta BIGINT NOT NULL, opened_delta BIGINT NOT NULL, units_delta BIGINT NOT NULL,
        pack_remaining_after BIGINT NOT NULL CHECK(pack_remaining_after>=0),
        CHECK(abs(closed_delta::NUMERIC)<=99999999999
          AND abs(opened_delta::NUMERIC)<=99999999999
          AND abs(units_delta::NUMERIC)<=99999999999),
        CHECK(
          (kind='initialize' AND closed_delta>0 AND opened_delta=0 AND units_delta>0
            AND pack_id IS NULL AND pack_remaining_after=0)
          OR (kind='pack_take' AND closed_delta<0 AND opened_delta=0 AND units_delta<0
            AND pack_id IS NULL AND pack_remaining_after=0)
          OR (kind='unit_take' AND closed_delta=0 AND opened_delta<0 AND units_delta=opened_delta
            AND pack_id IS NOT NULL)
          OR (kind='unit_open' AND closed_delta<0 AND opened_delta>=0 AND units_delta<0
            AND pack_remaining_after=opened_delta)),
        UNIQUE(tenant_id,operation_id,entry_order),
        FOREIGN KEY(tenant_id,operation_id,branch_id,catalog_id)
          REFERENCES public.unit_stock_operation(tenant_id,id,branch_id,catalog_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,lot_id,branch_id,catalog_id)
          REFERENCES public.unit_stock_lot(tenant_id,id,branch_id,catalog_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,pack_id,lot_id,branch_id,catalog_id)
          REFERENCES public.unit_stock_opened_pack(tenant_id,id,lot_id,branch_id,catalog_id)
          ON DELETE RESTRICT
      )
    """)
    _sql("""
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_stock_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_stock_reference_grants;
      CREATE INDEX ix_unit_stock_lot_scope
        ON public.unit_stock_lot(tenant_id,branch_id,catalog_id,id);
      CREATE INDEX ix_unit_stock_pack_lot ON public.unit_stock_opened_pack(tenant_id,lot_id,id);
      CREATE INDEX ix_unit_stock_entry_lot ON public.unit_stock_entry(tenant_id,lot_id);
      CREATE INDEX ix_unit_stock_entry_pack ON public.unit_stock_entry(tenant_id,pack_id);
      CREATE UNIQUE INDEX uq_unit_stock_lot_initialize ON public.unit_stock_entry(tenant_id,lot_id)
        WHERE kind='initialize';
    """)
    _sql(GUARD_SQL)
    for function in (
        "trg_unit_stock_guard",
        "trg_unit_stock_entry_apply",
        "trg_unit_stock_complete",
    ):
        _sql(f"""
          ALTER FUNCTION public.{function}() OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC,aurum_app,aurum_support;
        """)
    for table in TABLES:
        insert_permission = {
            "unit_stock_operation": (
                "CASE WHEN kind='initialize' THEN 'incoming.finalize' ELSE 'pos.sell' END"
            ),
            "unit_stock_lot": "'incoming.finalize'",
            "unit_stock_opened_pack": "'pos.sell'",
            "unit_stock_entry": (
                "CASE WHEN kind='initialize' THEN 'incoming.finalize' ELSE 'pos.sell' END"
            ),
        }[table]
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          GRANT SELECT,INSERT ON public.{table} TO aurum_app,aurum_support;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner
            USING(true) WITH CHECK(true);
          CREATE POLICY support ON public.{table} TO aurum_support USING(true) WITH CHECK(true);
          CREATE POLICY tenant_read ON public.{table} FOR SELECT TO aurum_app USING(
            tenant_id=public.current_tenant_id() AND (
              public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id)
              OR public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id)
              OR public.tenant_actor_has_scoped_permission(
                tenant_id,'incoming.finalize',branch_id)));
          CREATE POLICY tenant_insert ON public.{table} FOR INSERT TO aurum_app WITH CHECK(
            tenant_id=public.current_tenant_id() AND
            public.tenant_actor_has_scoped_permission(
              tenant_id,{insert_permission},branch_id));
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('inventory');
          CREATE TRIGGER a10_unit_stock_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_guard();
          CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
          CREATE CONSTRAINT TRIGGER unit_stock_complete AFTER INSERT ON public.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_complete();
        """)
        if table in ("unit_stock_lot", "unit_stock_opened_pack"):
            # FOR UPDATE needs a matching UPDATE USING policy as well as SELECT.
            # No application update may pass WITH CHECK; the mutation guard also
            # rejects direct writes before their proposed values are checked.
            _sql(f"""
              CREATE POLICY tenant_lock ON public.{table} FOR UPDATE TO aurum_app
                USING(tenant_id=public.current_tenant_id() AND (
                  public.tenant_actor_has_scoped_permission(tenant_id,'batches.view',branch_id)
                  OR public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id)
                  OR public.tenant_actor_has_scoped_permission(
                    tenant_id,'incoming.finalize',branch_id)))
                WITH CHECK(false);
            """)
    _sql("""
      GRANT UPDATE(closed_packs) ON public.unit_stock_lot TO aurum_app,aurum_support;
      GRANT UPDATE(remaining_units) ON public.unit_stock_opened_pack TO aurum_app,aurum_support;
      CREATE TRIGGER b10_unit_stock_apply AFTER INSERT ON public.unit_stock_entry
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_stock_entry_apply();
    """)


def downgrade() -> None:
    # Conducted integer stock must never be erased by a rollback.
    _sql("""
      LOCK TABLE public.unit_stock_operation,public.unit_stock_lot,
        public.unit_stock_opened_pack,public.unit_stock_entry IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.unit_stock_operation)
          OR EXISTS(SELECT 1 FROM public.unit_stock_lot)
          OR EXISTS(SELECT 1 FROM public.unit_stock_opened_pack)
          OR EXISTS(SELECT 1 FROM public.unit_stock_entry) THEN
          RAISE EXCEPTION 'unit_stock_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
    for function in (
        "trg_unit_stock_complete",
        "trg_unit_stock_entry_apply",
        "trg_unit_stock_guard",
    ):
        op.execute(f"DROP FUNCTION public.{function}()")
