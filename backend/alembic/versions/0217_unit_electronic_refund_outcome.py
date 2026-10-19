"""Persist terminal evidence for activated electronic refunds, without posting money.

Revision ID: 0217
Revises: 0216
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

revision = "0217"
down_revision = "0216"
branch_labels = None
depends_on = None

TABLE = "pos_unit_electronic_refund_outcome"
FUNCTIONS = (
    "trg_unit_electronic_refund_outcome_guard()",
    "trg_guard_unit_refund_outcome_cross_type()",
)


def _sql(sql: str) -> None:
    spec = spec_from_file_location(
        "aurum_0217_sql", Path(__file__).with_name("0171_unit_cash_intents.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


SQL = """
CREATE FUNCTION public.trg_unit_electronic_refund_outcome_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor UUID; activation public.pos_unit_electronic_refund_activation%ROWTYPE;
 review public.pos_unit_electronic_refund_intent%ROWTYPE; payment JSONB;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_electronic_refund_outcome_immutable' USING ERRCODE='55000';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'unit_electronic_refund_outcome_isolation_required' USING ERRCODE='23514';
 END IF;
 actor:=public.current_app_user_id();
 IF session_user<>'aurum_app' OR public.is_support_session() OR actor IS NULL
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.actor_id IS DISTINCT FROM actor THEN
  RAISE EXCEPTION 'unit_electronic_refund_outcome_actor_denied' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   'unit-electronic-refund-review:'||NEW.tenant_id||':'||NEW.operation_id,0));
 PERFORM 1 FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_sale_id FOR UPDATE;
 SELECT * INTO activation FROM public.pos_unit_electronic_refund_activation
   WHERE tenant_id=NEW.tenant_id AND id=NEW.operation_id;
 IF NOT FOUND OR (activation.parent_sale_id,activation.branch_id,activation.actor_id)
   IS DISTINCT FROM (NEW.parent_sale_id,NEW.branch_id,actor) THEN
  RAISE EXCEPTION 'unit_electronic_refund_outcome_activation_invalid' USING ERRCODE='23514';
 END IF;
 SELECT * INTO review FROM public.pos_unit_electronic_refund_intent
   WHERE tenant_id=NEW.tenant_id AND id=NEW.operation_id;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'unit_electronic_refund_outcome_review_invalid' USING ERRCODE='23514';
 END IF;
 SELECT value INTO payment FROM jsonb_array_elements(review.result_payload->'payments')
   WHERE value->>'original_payment_id'=NEW.original_payment_id::TEXT;
 IF payment IS NULL OR payment->>'method' IS DISTINCT FROM NEW.method
   OR payment->>'currency' IS DISTINCT FROM NEW.currency
   OR (payment->>'amount')::NUMERIC IS DISTINCT FROM NEW.amount THEN
  RAISE EXCEPTION 'unit_electronic_refund_outcome_payment_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   NEW.tenant_id::TEXT||chr(31)||NEW.terminal_id||chr(31)||NEW.document_number,0));
 IF EXISTS(SELECT 1 FROM public.pos_payment_attempt
     WHERE tenant_id=NEW.tenant_id AND terminal_id=NEW.terminal_id
       AND external_reference=NEW.document_number)
   OR EXISTS(SELECT 1 FROM public.pos_refund_reference
     WHERE tenant_id=NEW.tenant_id AND terminal_id=NEW.terminal_id
       AND document_number=NEW.document_number) THEN
  RAISE unique_violation USING MESSAGE='Terminal evidence was already used',
    CONSTRAINT='uq_pos_terminal_evidence_cross_type';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=actor AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.refund',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_electronic_refund_outcome_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=greatest(clock_timestamp(),activation.created_at);
 NEW.created_by:=actor;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_guard_unit_refund_outcome_cross_type() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE document TEXT;
BEGIN
 IF TG_TABLE_NAME='pos_payment_attempt' THEN
  document:=NEW.external_reference;
 ELSE
  document:=NEW.document_number;
 END IF;
 IF NEW.terminal_id IS NULL OR document IS NULL THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   NEW.tenant_id::TEXT||chr(31)||NEW.terminal_id||chr(31)||document,0));
 IF EXISTS(SELECT 1 FROM public.pos_unit_electronic_refund_outcome
   WHERE tenant_id=NEW.tenant_id AND terminal_id=NEW.terminal_id
     AND document_number=document) THEN
  RAISE unique_violation USING MESSAGE='Terminal evidence was already used',
    CONSTRAINT='uq_pos_terminal_evidence_cross_type';
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _sql(f"""
      CREATE TEMP TABLE electronic_refund_outcome_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.pos_unit_electronic_refund_activation'::regclass,
          'public.app_user'::regclass);
      GRANT REFERENCES ON public.pos_unit_electronic_refund_activation,public.app_user
        TO aurum_schema_owner;
      CREATE TABLE public.{TABLE} (
        tenant_id UUID NOT NULL,id UUID NOT NULL,operation_id UUID NOT NULL,
        parent_sale_id UUID NOT NULL,branch_id UUID NOT NULL,
        actor_id UUID NOT NULL REFERENCES public.app_user(id),original_payment_id UUID NOT NULL,
        method TEXT NOT NULL CHECK(method IN ('card','qr')),
        amount NUMERIC(14,2) NOT NULL CHECK(amount>0),
        currency TEXT NOT NULL CHECK(currency='TJS'),
        terminal_id TEXT NOT NULL CHECK(length(terminal_id) BETWEEN 1 AND 64
          AND terminal_id=btrim(terminal_id) AND terminal_id !~ '[[:cntrl:]]'),
        document_number TEXT NOT NULL CHECK(length(document_number) BETWEEN 1 AND 128
          AND document_number=btrim(document_number) AND document_number !~ '[[:cntrl:]]'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,operation_id)
          REFERENCES public.pos_unit_electronic_refund_activation(tenant_id,id),
        UNIQUE(tenant_id,operation_id,original_payment_id),
        UNIQUE(tenant_id,terminal_id,document_number),
        CHECK(substring(id::TEXT,15,1)='4' AND substring(id::TEXT,20,1) IN ('8','9','a','b'))
      );
      ALTER TABLE public.{TABLE} OWNER TO aurum_schema_owner;
      ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.{TABLE} FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.{TABLE} FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.{TABLE} TO aurum_app;
      GRANT SELECT ON public.{TABLE} TO aurum_support;
      CREATE POLICY schema_owner ON public.{TABLE} TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.{TABLE} FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.{TABLE} TO aurum_app USING(
        current_user='aurum_app' AND session_user='aurum_app'
        AND NOT public.is_tenant_support_session()
        AND tenant_id=public.current_tenant_id() AND actor_id=public.current_app_user_id()
        AND EXISTS(SELECT 1 FROM public.app_user
          WHERE id=public.current_app_user_id() AND status='active')
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id() AND actor_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.refund',branch_id));
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.electronic_refund_outcome_reference_grants
          WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON TABLE %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.electronic_refund_outcome_reference_grants;
    """)
    _sql(SQL)
    for function in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{function} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC,aurum_app,aurum_support;
        """)
    _sql(f"""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_unit_electronic_refund_outcome_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.{FUNCTIONS[0]};
      CREATE TRIGGER z90_unit_electronic_refund_outcome_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log();
      CREATE TRIGGER trg_unit_outcome_payment_cross_type
        BEFORE INSERT OR UPDATE OF terminal_id,external_reference ON public.pos_payment_attempt
        FOR EACH ROW EXECUTE FUNCTION public.{FUNCTIONS[1]};
      CREATE TRIGGER trg_unit_outcome_refund_cross_type
        BEFORE INSERT OR UPDATE OF terminal_id,document_number ON public.pos_refund_reference
        FOR EACH ROW EXECUTE FUNCTION public.{FUNCTIONS[1]};
    """)


def downgrade() -> None:
    _sql(f"""
      LOCK TABLE public.{TABLE} IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.{TABLE}) THEN
          RAISE EXCEPTION 'unit_electronic_refund_outcome_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER trg_unit_outcome_payment_cross_type ON public.pos_payment_attempt;
      DROP TRIGGER trg_unit_outcome_refund_cross_type ON public.pos_refund_reference;
      DROP TABLE public.{TABLE};
      DROP FUNCTION public.{FUNCTIONS[0]};
      DROP FUNCTION public.{FUNCTIONS[1]};
    """)
