"""Link prescription evidence to unit cart rows without activating checkout.

Revision ID: 0172
Revises: 0171
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0172"
down_revision: str | Sequence[str] | None = "0171"
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
CREATE FUNCTION public.trg_unit_prescription_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_log public.prescription_log%ROWTYPE; v_sale public.sale%ROWTYPE;
 v_shift public.shift%ROWTYPE; v_item public.pos_unit_cart_item%ROWTYPE;
 v_catalog public.tenant_catalog%ROWTYPE; v_actor UUID; v_register_active BOOLEAN;
BEGIN
 -- Preserve the existing legacy/whole-sale prescription mutation policy.
 IF TG_OP='INSERT' THEN
  IF NEW.unit_cart_item_id IS NULL THEN RETURN NEW; END IF;
  v_log:=NEW;
 ELSIF TG_OP='DELETE' THEN
  IF OLD.unit_cart_item_id IS NULL THEN RETURN OLD; END IF;
  v_log:=OLD;
 ELSE
  IF OLD.unit_cart_item_id IS NULL AND NEW.unit_cart_item_id IS NULL THEN
   RETURN NEW;
  END IF;
  IF (NEW.id,NEW.tenant_id,NEW.sale_id,NEW.sale_item_id,NEW.unit_cart_item_id,
      NEW.created_at,NEW.created_by) IS DISTINCT FROM
     (OLD.id,OLD.tenant_id,OLD.sale_id,OLD.sale_item_id,OLD.unit_cart_item_id,
      OLD.created_at,OLD.created_by) THEN
   RAISE EXCEPTION 'unit_prescription_identity_immutable' USING ERRCODE='55000';
  END IF;
  v_log:=NEW;
 END IF;
 v_actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session() OR v_actor IS NULL
   OR v_log.tenant_id IS DISTINCT FROM public.current_tenant_id() THEN
  RAISE EXCEPTION 'unit_prescription_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO v_sale FROM public.sale
   WHERE tenant_id=v_log.tenant_id AND id=v_log.sale_id FOR UPDATE;
 IF NOT FOUND OR v_sale.status<>'draft' OR v_sale.sale_type<>'sale'
   OR v_sale.parent_sale_id IS NOT NULL OR v_sale.currency<>'TJS'
   OR v_sale.cashier_user_id IS DISTINCT FROM v_actor THEN
  RAISE EXCEPTION 'unit_prescription_sale_invalid' USING ERRCODE='23514';
 END IF;
 IF NOT public.tenant_actor_has_scoped_permission(v_sale.tenant_id,'pos.sell',v_sale.branch_id)
   OR NOT public.tenant_actor_has_scoped_permission(
       v_sale.tenant_id,'pos.handle_prescription',v_sale.branch_id) THEN
  RAISE EXCEPTION 'unit_prescription_actor_denied' USING ERRCODE='42501';
 END IF;
 SELECT * INTO v_shift FROM public.shift
   WHERE tenant_id=v_sale.tenant_id AND id=v_sale.shift_id FOR UPDATE;
 IF NOT FOUND OR v_shift.status<>'open' OR v_shift.currency<>'TJS'
   OR (v_shift.branch_id,v_shift.register_id,v_shift.opened_by_user_id)
      IS DISTINCT FROM (v_sale.branch_id,v_sale.register_id,v_actor) THEN
  RAISE EXCEPTION 'unit_prescription_shift_invalid' USING ERRCODE='23514';
 END IF;
 SELECT r.is_active INTO v_register_active FROM public.register r
   WHERE r.tenant_id=v_sale.tenant_id AND r.id=v_sale.register_id
     AND r.branch_id=v_sale.branch_id FOR SHARE;
 IF v_register_active IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'unit_prescription_register_invalid' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM public.sale_payment
      WHERE tenant_id=v_sale.tenant_id AND sale_id=v_sale.id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
      WHERE tenant_id=v_sale.tenant_id AND sale_id=v_sale.id) THEN
  RAISE EXCEPTION 'unit_prescription_payment_started' USING ERRCODE='23514';
 END IF;
 -- Cleanup must still work if the linked medicine's dispensing policy changed.
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 SELECT * INTO v_item FROM public.pos_unit_cart_item
   WHERE tenant_id=v_log.tenant_id AND sale_id=v_log.sale_id AND id=v_log.unit_cart_item_id;
 IF NOT FOUND OR v_item.branch_id IS DISTINCT FROM v_sale.branch_id THEN
  RAISE EXCEPTION 'unit_prescription_cart_item_invalid' USING ERRCODE='23514';
 END IF;
 SELECT * INTO v_catalog FROM public.tenant_catalog
   WHERE tenant_id=v_item.tenant_id AND id=v_item.catalog_id FOR SHARE;
 IF NOT FOUND OR v_catalog.is_active IS DISTINCT FROM true
   OR v_catalog.deleted_at IS NOT NULL OR v_catalog.currency<>'TJS'
   OR v_catalog.dispensing_type IS DISTINCT FROM 'prescription' THEN
  RAISE EXCEPTION 'unit_prescription_catalog_invalid' USING ERRCODE='23514';
 END IF;
 IF nullif(regexp_replace(concat_ws('',NEW.prescription_number,NEW.doctor_name,
      NEW.doctor_license,NEW.patient_name,NEW.notes),'[[:space:]]','','g'),'') IS NULL THEN
  RAISE EXCEPTION 'unit_prescription_detail_required' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
  NEW.created_at:=statement_timestamp(); NEW.created_by:=v_actor;
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_prescription_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid='public.pos_unit_cart_item'::regclass;
      GRANT REFERENCES ON public.pos_unit_cart_item TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_cart_item ADD CONSTRAINT uq_unit_cart_prescription_scope
        UNIQUE(tenant_id,sale_id,id);
      ALTER TABLE public.prescription_log ADD COLUMN unit_cart_item_id UUID;
      ALTER TABLE public.prescription_log ADD CONSTRAINT ck_prescription_item_kind
        CHECK(sale_item_id IS NULL OR unit_cart_item_id IS NULL);
      ALTER TABLE public.prescription_log ADD CONSTRAINT fk_prescription_unit_cart_item
        FOREIGN KEY(tenant_id,sale_id,unit_cart_item_id)
        REFERENCES public.pos_unit_cart_item(tenant_id,sale_id,id) ON DELETE RESTRICT;
      CREATE INDEX ix_prescription_unit_cart_item
        ON public.prescription_log(tenant_id,sale_id,unit_cart_item_id)
        WHERE unit_cart_item_id IS NOT NULL;
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_prescription_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_prescription_reference_grants;
    """)
    _sql(FUNCTIONS)
    _sql("""
      ALTER FUNCTION public.trg_unit_prescription_guard() OWNER TO aurum_schema_owner;
      REVOKE ALL ON FUNCTION public.trg_unit_prescription_guard()
        FROM PUBLIC,aurum_app,aurum_support;
      CREATE TRIGGER a10_unit_prescription_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.prescription_log FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_prescription_guard();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.prescription_log,public.pos_unit_cart_item IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.prescription_log WHERE unit_cart_item_id IS NOT NULL) THEN
          RAISE EXCEPTION 'unit_prescription_nonempty_downgrade_forbidden' USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER a10_unit_prescription_guard ON public.prescription_log;
      DROP FUNCTION public.trg_unit_prescription_guard();
      ALTER TABLE public.prescription_log DROP CONSTRAINT fk_prescription_unit_cart_item;
      ALTER TABLE public.prescription_log DROP CONSTRAINT ck_prescription_item_kind;
      DROP INDEX public.ix_prescription_unit_cart_item;
      ALTER TABLE public.prescription_log DROP COLUMN unit_cart_item_id;
      ALTER TABLE public.pos_unit_cart_item DROP CONSTRAINT uq_unit_cart_prescription_scope;
    """)
