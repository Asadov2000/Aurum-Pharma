"""Draft unit cart intents, with legacy payment paths closed until v2 checkout.

Revision ID: 0170
Revises: 0169
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0170"
down_revision: str | Sequence[str] | None = "0169"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.execute(statement.replace(":", r"\:"))
            start = match.end()
    if sql[start:].strip():
        op.execute(sql[start:].replace(":", r"\:"))


FUNCTIONS = """
CREATE FUNCTION public.trg_unit_cart_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_sale public.sale%ROWTYPE; v_shift public.shift%ROWTYPE;
 v_catalog public.tenant_catalog%ROWTYPE; v_item public.pos_unit_cart_item%ROWTYPE;
 v_actor UUID; v_factor BIGINT; v_step BIGINT; v_price NUMERIC; v_gross NUMERIC;
 v_register_active BOOLEAN;
BEGIN
 IF TG_OP='DELETE' THEN v_item:=OLD; ELSE v_item:=NEW; END IF;
 IF TG_OP='UPDATE' AND (NEW.id,NEW.tenant_id,NEW.branch_id,NEW.sale_id,NEW.catalog_id,
      NEW.created_at,NEW.created_by) IS DISTINCT FROM
      (OLD.id,OLD.tenant_id,OLD.branch_id,OLD.sale_id,OLD.catalog_id,
       OLD.created_at,OLD.created_by) THEN
  RAISE EXCEPTION 'unit_cart_identity_immutable' USING ERRCODE='55000';
 END IF;
 v_actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR v_actor IS NULL OR v_item.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NOT public.tenant_actor_has_scoped_permission(v_item.tenant_id,'pos.sell',v_item.branch_id)
 THEN RAISE EXCEPTION 'unit_cart_actor_denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO v_sale FROM public.sale
   WHERE tenant_id=v_item.tenant_id AND id=v_item.sale_id FOR UPDATE;
 IF NOT FOUND OR v_sale.branch_id<>v_item.branch_id OR v_sale.cashier_user_id<>v_actor
   OR v_sale.status<>'draft' OR v_sale.sale_type<>'sale' OR v_sale.currency<>'TJS'
 THEN RAISE EXCEPTION 'unit_cart_sale_invalid' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_shift FROM public.shift
   WHERE tenant_id=v_sale.tenant_id AND id=v_sale.shift_id FOR UPDATE;
 IF NOT FOUND OR v_shift.status<>'open' OR v_shift.branch_id<>v_sale.branch_id
   OR v_shift.register_id<>v_sale.register_id OR v_shift.opened_by_user_id<>v_actor
   OR v_shift.currency<>'TJS'
 THEN RAISE EXCEPTION 'unit_cart_shift_invalid' USING ERRCODE='23514'; END IF;
 SELECT r.is_active INTO v_register_active FROM public.register r
   WHERE r.tenant_id=v_sale.tenant_id AND r.id=v_sale.register_id
     AND r.branch_id=v_sale.branch_id FOR SHARE;
 IF v_register_active IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'unit_cart_shift_invalid' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM public.sale_payment WHERE tenant_id=v_sale.tenant_id
      AND sale_id=v_sale.id) OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
      WHERE tenant_id=v_sale.tenant_id AND sale_id=v_sale.id) THEN
  RAISE EXCEPTION 'unit_cart_payment_started' USING ERRCODE='23514';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 SELECT * INTO v_catalog FROM public.tenant_catalog
   WHERE tenant_id=NEW.tenant_id AND id=NEW.catalog_id FOR SHARE;
 IF NOT FOUND OR NOT v_catalog.is_active OR v_catalog.deleted_at IS NOT NULL
    OR v_catalog.currency<>'TJS' OR v_catalog.unit_sale_preparation IS NULL THEN
  RAISE EXCEPTION 'unit_cart_catalog_invalid' USING ERRCODE='23514';
 END IF;
 v_factor:=(v_catalog.unit_sale_preparation->>'units_per_pack')::BIGINT;
 v_step:=(v_catalog.unit_sale_preparation->>'dispense_step')::BIGINT;
 IF NEW.sale_format='pack' THEN v_price:=v_catalog.base_price;
 ELSE v_price:=(v_catalog.unit_sale_preparation->>'unit_price')::NUMERIC; END IF;
 IF (NEW.sale_format='unit' AND NEW.quantity % v_step<>0)
   OR (NEW.sale_format='pack' AND NEW.quantity*v_factor>99999999999)
   OR v_price IS NULL OR v_price<=0 THEN
  RAISE EXCEPTION 'unit_cart_quantity_invalid' USING ERRCODE='23514';
 END IF;
 v_gross:=v_price*NEW.quantity;
 IF v_gross>999999999999.99 OR NEW.discount_amount>=v_gross THEN
  RAISE EXCEPTION 'unit_cart_discount_invalid' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN NEW.created_at:=statement_timestamp(); NEW.created_by:=v_actor; END IF;
 NEW.updated_at:=statement_timestamp(); NEW.updated_by:=v_actor;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_unit_cart_payment_boundary() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_sale_id UUID; v_tenant UUID;
BEGIN
 IF TG_TABLE_NAME='sale' THEN
  IF TG_OP='DELETE' THEN v_sale_id:=OLD.id; v_tenant:=OLD.tenant_id;
  ELSE
   v_sale_id:=NEW.id; v_tenant:=NEW.tenant_id;
   IF NEW.status='draft' AND NEW.receipt_number IS NULL AND NEW.completed_at IS NULL
      AND (NEW.tenant_id,NEW.branch_id,NEW.register_id,NEW.shift_id,NEW.cashier_user_id,
           NEW.sale_type,NEW.currency) IS NOT DISTINCT FROM
          (OLD.tenant_id,OLD.branch_id,OLD.register_id,OLD.shift_id,OLD.cashier_user_id,
           OLD.sale_type,OLD.currency) THEN RETURN NEW; END IF;
  END IF;
 ELSE
  v_sale_id:=NEW.sale_id; v_tenant:=NEW.tenant_id;
  PERFORM 1 FROM public.sale WHERE id=v_sale_id AND tenant_id=v_tenant FOR UPDATE;
 END IF;
 IF EXISTS(SELECT 1 FROM public.pos_unit_cart_item
      WHERE tenant_id=v_tenant AND sale_id=v_sale_id) THEN
  RAISE EXCEPTION 'unit_cart_checkout_not_ready' USING ERRCODE='23514';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_cart_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.tenant'::regclass,'public.branch'::regclass,
          'public.tenant_catalog'::regclass,'public.app_user'::regclass,'public.sale'::regclass);
      GRANT REFERENCES ON public.tenant,public.branch,public.tenant_catalog,public.app_user,
        public.sale TO aurum_schema_owner;
      CREATE TABLE public.pos_unit_cart_item (
        id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES public.tenant(id),
        branch_id UUID NOT NULL, sale_id UUID NOT NULL, catalog_id UUID NOT NULL,
        position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 200),
        sale_format TEXT NOT NULL CHECK(sale_format IN ('pack','unit')),
        quantity BIGINT NOT NULL CHECK(quantity BETWEEN 1 AND 99999999999),
        discount_amount NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(discount_amount>=0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID REFERENCES public.app_user(id),
        updated_by UUID REFERENCES public.app_user(id),
        FOREIGN KEY(tenant_id,branch_id) REFERENCES public.branch(tenant_id,id),
        FOREIGN KEY(tenant_id,catalog_id) REFERENCES public.tenant_catalog(tenant_id,id),
        CONSTRAINT fk_unit_cart_sale FOREIGN KEY(tenant_id,sale_id)
          REFERENCES public.sale(tenant_id,id) ON DELETE RESTRICT,
        CONSTRAINT uq_unit_cart_sale_position UNIQUE(tenant_id,sale_id,position)
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_cart_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_cart_reference_grants;
      ALTER TABLE public.pos_unit_cart_item OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_cart_item ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_unit_cart_item FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_unit_cart_item FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT,UPDATE,DELETE ON public.pos_unit_cart_item TO aurum_app;
      GRANT SELECT ON public.pos_unit_cart_item TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_unit_cart_item TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_unit_cart_item FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_unit_cart_item TO aurum_app USING(
        tenant_id=public.current_tenant_id() AND
        public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id) AND
        EXISTS(SELECT 1 FROM public.sale s WHERE s.tenant_id=pos_unit_cart_item.tenant_id
          AND s.id=pos_unit_cart_item.sale_id AND s.cashier_user_id=public.current_app_user_id()))
        WITH CHECK(tenant_id=public.current_tenant_id() AND
        public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id) AND
        EXISTS(SELECT 1 FROM public.sale s WHERE s.tenant_id=pos_unit_cart_item.tenant_id
          AND s.id=pos_unit_cart_item.sale_id AND s.cashier_user_id=public.current_app_user_id()));
    """)
    _sql(FUNCTIONS)
    for name in ("trg_unit_cart_guard", "trg_unit_cart_payment_boundary"):
        _sql(f"""
          ALTER FUNCTION public.{name}() OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC,aurum_app,aurum_support;
        """)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_cart_item FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access();
      CREATE TRIGGER a10_unit_cart_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_cart_item FOR EACH ROW EXECUTE FUNCTION public.trg_unit_cart_guard();
      CREATE TRIGGER z90_unit_cart_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_unit_cart_item FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
      CREATE TRIGGER a05_unit_cart_payment_boundary BEFORE UPDATE OR DELETE ON public.sale
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_cart_payment_boundary();
      CREATE TRIGGER a05_unit_cart_payment_boundary BEFORE INSERT OR UPDATE ON public.sale_payment
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_cart_payment_boundary();
      CREATE TRIGGER a05_unit_cart_payment_boundary BEFORE INSERT OR UPDATE
        ON public.pos_payment_attempt
        FOR EACH ROW EXECUTE FUNCTION public.trg_unit_cart_payment_boundary();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_cart_item IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_cart_item) THEN
          RAISE EXCEPTION 'unit_cart_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER a05_unit_cart_payment_boundary ON public.sale;
      DROP TRIGGER a05_unit_cart_payment_boundary ON public.sale_payment;
      DROP TRIGGER a05_unit_cart_payment_boundary ON public.pos_payment_attempt;
      DROP TABLE public.pos_unit_cart_item;
      DROP FUNCTION public.trg_unit_cart_guard();
      DROP FUNCTION public.trg_unit_cart_payment_boundary();
    """)
