"""Fence cancelled physical cash attempts without changing their saved evidence.

Revision ID: 0237
Revises: 0236
"""

import re

from alembic import op

revision = "0237"
down_revision = "0236"
branch_labels = None
depends_on = None


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.get_bind().exec_driver_sql(statement)
            start = match.end()
    if sql[start:].strip():
        op.get_bind().exec_driver_sql(sql[start:].strip())


FUNCTIONS = """
CREATE FUNCTION public.trg_physical_cash_cancellation_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s public.sale%ROWTYPE; i public.pos_unit_cash_intent%ROWTYPE; actor UUID;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'physical_cash_cancellation_immutable' USING ERRCODE='55000';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR public.is_tenant_support_session() OR actor IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.cashier_user_id IS DISTINCT FROM actor
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'physical_cash_cancellation_actor_denied' USING ERRCODE='42501';
 END IF;
 -- Completion and cancellation serialize on this same parent row, not a status snapshot.
 SELECT * INTO s FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id FOR UPDATE;
 SELECT * INTO i FROM public.pos_unit_cash_intent
   WHERE tenant_id=NEW.tenant_id AND id=NEW.id;
 IF s.id IS NULL OR i.id IS NULL OR s.status<>'draft' OR s.sale_type<>'sale'
   OR s.is_test OR s.currency<>'TJS' OR s.parent_sale_id IS NOT NULL
   OR (s.branch_id,s.register_id,s.shift_id,s.cashier_user_id)
     IS DISTINCT FROM (NEW.branch_id,NEW.register_id,NEW.shift_id,NEW.cashier_user_id)
   OR (i.sale_id,i.branch_id,i.register_id,i.shift_id,i.cashier_user_id,i.request_hash)
     IS DISTINCT FROM (NEW.sale_id,NEW.branch_id,NEW.register_id,NEW.shift_id,
       NEW.cashier_user_id,NEW.request_hash)
   OR jsonb_typeof(i.quote_payload->'lines') IS DISTINCT FROM 'array'
   OR jsonb_array_length(i.quote_payload->'lines')=0
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(i.quote_payload->'lines') line
     WHERE line->>'inventory_kind' IS DISTINCT FROM 'physical_unit') THEN
  RAISE EXCEPTION 'physical_cash_cancellation_intent_invalid' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM public.sale_payment
      WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
      WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id)
   OR EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding b
      WHERE b.tenant_id=NEW.tenant_id AND b.sale_id=NEW.sale_id
        AND NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel c
          WHERE c.tenant_id=b.tenant_id AND c.id=b.id))
   OR EXISTS(SELECT 1 FROM public.unit_stock_cart_command_v2
      WHERE tenant_id=NEW.tenant_id
        AND (checkout_sale_id=NEW.sale_id OR mixed_checkout_sale_id=NEW.sale_id))
   OR EXISTS(SELECT 1 FROM public.pos_physical_checkout
      WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id)
   OR EXISTS(SELECT 1 FROM public.pos_mixed_checkout
      WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id) THEN
  RAISE EXCEPTION 'physical_cash_cancellation_payment_started' USING ERRCODE='23514';
 END IF;
 NEW.created_at:=statement_timestamp(); NEW.created_by:=actor;
 RETURN NEW;
END $$;

CREATE FUNCTION public.trg_cancelled_cash_checkout_fence() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 PERFORM 1 FROM public.sale
   WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM public.pos_physical_cash_cancellation
     WHERE tenant_id=NEW.tenant_id AND (id=NEW.id OR id=NEW.cash_intent_id)) THEN
  RAISE EXCEPTION 'physical_cash_operation_cancelled' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE physical_cash_cancellation_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.sale'::regclass,
          'public.pos_unit_cash_intent'::regclass);
      GRANT REFERENCES ON public.sale,public.pos_unit_cash_intent TO aurum_schema_owner;
      CREATE TABLE public.pos_physical_cash_cancellation (
        tenant_id UUID NOT NULL,id UUID NOT NULL,sale_id UUID NOT NULL,
        branch_id UUID NOT NULL,register_id UUID NOT NULL,shift_id UUID NOT NULL,
        cashier_user_id UUID NOT NULL,
        request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),created_by UUID NOT NULL,
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,id) REFERENCES public.pos_unit_cash_intent(tenant_id,id),
        FOREIGN KEY(tenant_id,sale_id) REFERENCES public.sale(tenant_id,id)
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.physical_cash_cancellation_reference_grants
          WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.physical_cash_cancellation_reference_grants;
      ALTER TABLE public.pos_physical_cash_cancellation OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_physical_cash_cancellation ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_physical_cash_cancellation FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_physical_cash_cancellation FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_physical_cash_cancellation TO aurum_app;
      GRANT SELECT ON public.pos_physical_cash_cancellation TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_physical_cash_cancellation
        TO aurum_schema_owner USING(true) WITH CHECK(true);
      CREATE POLICY support_read ON public.pos_physical_cash_cancellation
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_physical_cash_cancellation TO aurum_app
        USING(tenant_id=public.current_tenant_id()
          AND cashier_user_id=public.current_app_user_id()
          AND NOT public.is_support_session()
          AND NOT public.is_tenant_support_session()
          AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id()
          AND cashier_user_id=public.current_app_user_id()
          AND NOT public.is_support_session()
          AND NOT public.is_tenant_support_session()
          AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
    """)
    _sql(FUNCTIONS)
    for name in ("trg_physical_cash_cancellation_guard", "trg_cancelled_cash_checkout_fence"):
        _sql(f"""
          ALTER FUNCTION public.{name}() OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC,aurum_app,aurum_support;
        """)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_physical_cash_cancellation FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access();
      CREATE TRIGGER a10_physical_cash_cancellation_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_physical_cash_cancellation FOR EACH ROW
        EXECUTE FUNCTION public.trg_physical_cash_cancellation_guard();
      CREATE TRIGGER z90_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_physical_cash_cancellation FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)
    # Native/direct SQL keeps its existing invoker privileges. A denied marker rolls
    # back the same transaction's stock and payment writes under existing bindings.
    for table in ("pos_physical_checkout", "pos_mixed_checkout"):
        _sql(f"""
          CREATE TRIGGER a05_cancelled_cash_checkout_fence BEFORE INSERT ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_cancelled_cash_checkout_fence();
        """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_physical_cash_cancellation IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_physical_cash_cancellation) THEN
          RAISE EXCEPTION 'physical_cash_cancellation_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    for table in ("pos_physical_checkout", "pos_mixed_checkout"):
        _sql(f"DROP TRIGGER a05_cancelled_cash_checkout_fence ON public.{table};")
    _sql("""
      DROP TABLE public.pos_physical_cash_cancellation;
      DROP FUNCTION public.trg_physical_cash_cancellation_guard();
      DROP FUNCTION public.trg_cancelled_cash_checkout_fence();
    """)
