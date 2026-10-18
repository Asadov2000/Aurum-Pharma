"""Bind internal electronic physical checkout to an immutable payment plan.

Revision ID: 0212
Revises: 0211
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from sqlalchemy import text

from alembic import op

revision: str = "0212"
down_revision: str | Sequence[str] | None = "0211"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "pos_unit_payment_attempt_binding"
FUNCTIONS = (
    "unit_electronic_current_quote(uuid,uuid,uuid,uuid,uuid,uuid)",
    "assert_unit_electronic_checkout(uuid,uuid,boolean)",
    "assert_unit_electronic_payments(uuid,uuid,boolean)",
    "assert_unit_payment_attempt_binding(uuid,uuid)",
    "unit_electronic_payment_matches(uuid,uuid,uuid,text,numeric,text,uuid)",
    "unit_electronic_write_allowed(text,text,jsonb,jsonb)",
    "trg_unit_payment_attempt_binding_guard()",
    "trg_unit_payment_attempt_binding_complete()",
)


def _sql(sql: str) -> None:
    path = Path(__file__).with_name("0171_unit_cash_intents.py")
    spec = spec_from_file_location("aurum_0212_sql", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical SQL executor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sql(sql)


def _definition(signature: str) -> str:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": "public." + signature},
    )
    if not isinstance(definition, str):
        raise RuntimeError("Historical function is missing")
    return definition


def _replace(signature: str, old: str, new: str, *, upgrade: bool) -> None:
    definition = _definition(signature)
    before, after = (old, new) if upgrade else (new, old)
    if definition.count(before) != 1:
        raise RuntimeError(f"Electronic checkout historical guard changed: {signature}")
    op.get_bind().exec_driver_sql(definition.replace(before, after, 1))


SQL = """
CREATE FUNCTION public.unit_electronic_payment_matches(
 p_tenant UUID,p_sale UUID,p_id UUID,p_method TEXT,p_amount NUMERIC,
 p_currency TEXT,p_attempt UUID) RETURNS BOOLEAN
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM public.pos_physical_checkout m
   JOIN public.pos_unit_payment_plan_intent i
     ON i.tenant_id=m.tenant_id AND i.id=m.payment_plan_intent_id
   CROSS JOIN LATERAL jsonb_array_elements(i.request_payload->'payments') part
   WHERE m.tenant_id=p_tenant AND m.sale_id=p_sale AND m.payment_journal_version=3
     AND p_currency='TJS' AND part->>'method'=p_method
     AND (part->>'amount')::NUMERIC=p_amount AND (
       (p_method='cash' AND p_id=m.id AND p_attempt IS NULL)
       OR (p_method IN ('card','qr') AND p_attempt=p_id AND EXISTS(
         SELECT 1 FROM public.pos_unit_payment_attempt_binding b
         JOIN public.pos_payment_attempt a ON a.tenant_id=b.tenant_id AND a.id=b.id
         WHERE b.tenant_id=p_tenant AND b.id=p_id AND b.intent_id=i.id
           AND (b.sale_id,b.branch_id,b.cashier_user_id,b.method,b.amount,b.currency)
             IS NOT DISTINCT FROM (p_sale,m.branch_id,m.cashier_user_id,p_method,p_amount,'TJS')
           AND (a.sale_id,a.cashier_user_id,a.operation_id,a.payment_method,a.amount,a.currency)
             IS NOT DISTINCT FROM (p_sale,m.cashier_user_id,b.id,p_method,p_amount,'TJS')
           AND a.status IN ('confirmed','consumed') AND a.evidence_required))))
$$;
CREATE FUNCTION public.assert_unit_payment_attempt_binding(p_tenant UUID,p_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE b public.pos_unit_payment_attempt_binding%ROWTYPE; a public.pos_payment_attempt%ROWTYPE;
BEGIN
 SELECT * INTO b FROM public.pos_unit_payment_attempt_binding WHERE tenant_id=p_tenant AND id=p_id;
 IF b.id IS NULL THEN RETURN; END IF;
 SELECT * INTO a FROM public.pos_payment_attempt WHERE tenant_id=p_tenant AND id=p_id;
 IF a.id IS NULL OR
   (a.sale_id,a.cashier_user_id,a.operation_id,a.payment_method,a.amount,a.currency)
   IS DISTINCT FROM (b.sale_id,b.cashier_user_id,b.id,b.method,b.amount,b.currency)
   OR NOT a.evidence_required
   OR NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding p
     WHERE p.tenant_id=b.tenant_id AND p.id=b.intent_id AND p.sale_id=b.sale_id
       AND NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel c
         WHERE c.tenant_id=p.tenant_id AND c.id=p.id))
   OR (a.status='consumed' AND NOT EXISTS(SELECT 1 FROM public.pos_physical_checkout m
     JOIN public.sale s ON s.tenant_id=m.tenant_id AND s.id=m.sale_id
     WHERE m.tenant_id=b.tenant_id AND m.sale_id=b.sale_id AND m.payment_journal_version=3
       AND m.payment_plan_intent_id=b.intent_id AND s.status='completed'
       AND a.consumed_at=(m.result_payload->>'completed_at')::TIMESTAMPTZ)) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_invalid' USING ERRCODE='23514';
 END IF;
END $$;
CREATE FUNCTION public.trg_unit_payment_attempt_binding_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE i public.pos_unit_payment_plan_intent%ROWTYPE; q JSONB;
 settings public.tenant_settings%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_immutable' USING ERRCODE='55000';
 END IF;
 IF session_user<>'aurum_app' OR public.is_support_session()
   OR NEW.tenant_id IS DISTINCT FROM public.current_tenant_id()
   OR NEW.cashier_user_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.cashier_user_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_actor_denied' USING ERRCODE='42501';
 END IF;
 -- Match the POS namespace lock order before taking any intent or sale lock.
 PERFORM pg_advisory_xact_lock(hashtextextended(
   NEW.tenant_id::TEXT||':'||NEW.id::TEXT,0));
 IF EXISTS(SELECT 1 FROM public.pos_command
     WHERE tenant_id=NEW.tenant_id AND operation_id=NEW.id)
   OR EXISTS(SELECT 1 FROM public.sale
     WHERE tenant_id=NEW.tenant_id AND operation_id=NEW.id)
   OR EXISTS(SELECT 1 FROM public.sale_payment
     WHERE tenant_id=NEW.tenant_id AND operation_id=NEW.id)
   OR EXISTS(SELECT 1 FROM public.pos_refund_attempt
     WHERE tenant_id=NEW.tenant_id AND operation_id=NEW.id)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
     WHERE tenant_id=NEW.tenant_id AND operation_id=NEW.id) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_source_invalid' USING ERRCODE='23514';
 END IF;
 PERFORM 1 FROM public.sale WHERE tenant_id=NEW.tenant_id AND id=NEW.sale_id FOR UPDATE;
 SELECT * INTO i FROM public.pos_unit_payment_plan_intent
   WHERE tenant_id=NEW.tenant_id AND id=NEW.intent_id;
 IF i.id IS NULL OR NEW.id=NEW.intent_id OR (i.sale_id,i.branch_id,i.cashier_user_id)
      IS DISTINCT FROM (NEW.sale_id,NEW.branch_id,NEW.cashier_user_id)
   OR NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding b
     WHERE b.tenant_id=NEW.tenant_id AND b.id=i.id AND b.sale_id=NEW.sale_id
       AND NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel c
         WHERE c.tenant_id=b.tenant_id AND c.id=b.id))
   OR NEW.method NOT IN ('card','qr') OR NEW.currency<>'TJS'
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(i.request_payload->'payments') p
     WHERE p->>'method'=NEW.method AND (p->>'amount')::NUMERIC=NEW.amount)
   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt WHERE id=NEW.id)
   OR EXISTS(SELECT 1 FROM public.sale_payment WHERE sale_id=NEW.sale_id) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_source_invalid' USING ERRCODE='23514';
 END IF;
 q:=public.unit_electronic_current_quote(i.tenant_id,i.sale_id,i.branch_id,
   i.register_id,i.shift_id,i.cashier_user_id);
 SELECT * INTO settings FROM public.tenant_settings WHERE tenant_id=i.tenant_id FOR SHARE;
 IF q IS DISTINCT FROM i.quote_payload
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') p
     WHERE p->>'inventory_kind' IS DISTINCT FROM 'physical_unit')
   OR NOT EXISTS(SELECT 1 FROM public.sale s WHERE s.tenant_id=i.tenant_id AND s.id=i.sale_id
     AND s.status='draft' AND NOT s.is_test AND s.parent_sale_id IS NULL)
   OR settings.tenant_id IS NULL OR NOT settings.pos_payment_methods ?& ARRAY(
     SELECT p->>'method' FROM jsonb_array_elements(i.request_payload->'payments') p)
   OR (jsonb_array_length(i.request_payload->'payments')>1
     AND NOT settings.pos_mixed_payment_enabled) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_quote_invalid' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM public.pos_unit_cart_item cart
   JOIN public.tenant_catalog catalog
     ON catalog.tenant_id=cart.tenant_id AND catalog.id=cart.catalog_id
   WHERE cart.tenant_id=i.tenant_id AND cart.sale_id=i.sale_id
     AND (catalog.dispensing_type='special' OR (catalog.dispensing_type='prescription'
       AND NOT EXISTS(SELECT 1 FROM public.prescription_log p
         WHERE p.tenant_id=cart.tenant_id AND p.sale_id=cart.sale_id
           AND (p.unit_cart_item_id=cart.id OR
             (p.unit_cart_item_id IS NULL AND p.sale_item_id IS NULL))
           AND regexp_replace(concat_ws('',p.prescription_number,p.doctor_name,p.doctor_license,
             p.patient_name,p.notes),'[[:space:]]','','g')<>'')))) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_prescription_required' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.cashier_user_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at:=clock_timestamp(); NEW.created_by:=NEW.cashier_user_id;
 RETURN NEW;
END $$;
CREATE FUNCTION public.trg_unit_payment_attempt_binding_complete() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 PERFORM public.assert_unit_payment_attempt_binding(NEW.tenant_id,NEW.id);
 RETURN NEW;
END $$;
CREATE FUNCTION public.assert_unit_electronic_payments(p_tenant UUID,p_sale UUID,p_final BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE m public.pos_physical_checkout%ROWTYPE; i public.pos_unit_payment_plan_intent%ROWTYPE;
 expected JSONB; parts BIGINT; electronics BIGINT;
BEGIN
 SELECT * INTO m FROM public.pos_physical_checkout WHERE tenant_id=p_tenant AND sale_id=p_sale;
 SELECT * INTO i FROM public.pos_unit_payment_plan_intent
   WHERE tenant_id=p_tenant AND id=m.payment_plan_intent_id;
 SELECT count(*),count(*) FILTER(WHERE part->>'method'<>'cash'),
   jsonb_agg(jsonb_build_object('id',CASE WHEN part->>'method'='cash' THEN m.id ELSE b.id END,
     'method',part->>'method','amount',part->>'amount','currency','TJS')
     ORDER BY (part->>'method') COLLATE "C") INTO parts,electronics,expected
   FROM jsonb_array_elements(i.request_payload->'payments') part
   LEFT JOIN public.pos_unit_payment_attempt_binding b ON b.tenant_id=p_tenant
     AND b.intent_id=i.id AND b.method=part->>'method';
 IF m.id IS NULL OR i.id IS NULL OR m.payment_journal_version<>3
   OR parts NOT BETWEEN 1 AND 3 OR electronics<1
   OR m.result_payload->'payments' IS DISTINCT FROM expected
   OR (SELECT count(*) FROM public.pos_unit_payment_attempt_binding
     WHERE tenant_id=p_tenant AND intent_id=i.id)<>electronics
   OR (SELECT count(*) FROM public.pos_payment_attempt WHERE sale_id=p_sale)<>electronics
   OR NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding b
     WHERE b.tenant_id=p_tenant AND b.id=i.id AND b.sale_id=p_sale
       AND NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel c
         WHERE c.tenant_id=b.tenant_id AND c.id=b.id))
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(expected) p
     WHERE NOT public.unit_electronic_payment_matches(p_tenant,p_sale,(p->>'id')::UUID,
       p->>'method',(p->>'amount')::NUMERIC,'TJS',
       CASE WHEN p->>'method'='cash' THEN NULL::UUID ELSE (p->>'id')::UUID END))
   OR EXISTS(SELECT 1 FROM public.sale_payment p WHERE p.sale_id=p_sale AND (
     p.tenant_id<>p_tenant OR NOT public.unit_electronic_payment_matches(
       p_tenant,p_sale,p.id,p.payment_method,p.amount,p.currency,p.payment_attempt_id)
     OR p.operation_id IS DISTINCT FROM p.id OR p.operation_hash IS DISTINCT FROM m.request_hash
     OR p.metadata IS NOT NULL)) THEN
  RAISE EXCEPTION 'unit_electronic_payments_invalid' USING ERRCODE='23514';
 END IF;
 IF p_final AND (
   (SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)<>parts
   OR (SELECT count(*) FROM public.pos_unit_checkout_payment_component
     WHERE tenant_id=p_tenant AND sale_id=p_sale)<>parts
   OR EXISTS(SELECT 1 FROM public.pos_unit_payment_attempt_binding b
     JOIN public.pos_payment_attempt a ON a.tenant_id=b.tenant_id AND a.id=b.id
     WHERE b.tenant_id=p_tenant AND b.intent_id=i.id AND (
       a.status<>'consumed' OR a.consumed_at<a.confirmed_at OR a.consumed_at IS DISTINCT FROM
         (m.result_payload->>'completed_at')::TIMESTAMPTZ))
   OR EXISTS(SELECT 1 FROM public.pos_unit_checkout_payment_component c
     LEFT JOIN public.sale_payment p ON p.tenant_id=c.tenant_id AND p.id=c.id
     WHERE c.tenant_id=p_tenant AND c.sale_id=p_sale AND (
       c.physical_checkout_id IS DISTINCT FROM m.id OR c.mixed_checkout_id IS NOT NULL
       OR c.created_by IS DISTINCT FROM m.cashier_user_id OR p.id IS NULL
       OR (p.sale_id,p.payment_method,p.amount,p.currency,p.payment_attempt_id)
         IS DISTINCT FROM (c.sale_id,c.method,c.amount,c.currency,c.payment_attempt_id)
       OR NOT public.unit_electronic_payment_matches(p_tenant,p_sale,c.id,c.method,
         c.amount,c.currency,c.payment_attempt_id)))) THEN
  RAISE EXCEPTION 'unit_electronic_payments_incomplete' USING ERRCODE='23514';
 END IF;
END $$;
CREATE FUNCTION public.unit_electronic_write_allowed(
 p_table TEXT,p_op TEXT,p_new JSONB,p_old JSONB) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE tenant UUID; v_sale UUID; i public.pos_unit_payment_plan_intent%ROWTYPE;
 b public.pos_unit_payment_attempt_binding%ROWTYPE; m public.pos_physical_checkout%ROWTYPE;
 q JSONB; methods TEXT[]; settings public.tenant_settings%ROWTYPE;
BEGIN
 IF p_new IS NULL OR p_op='DELETE' OR session_user<>'aurum_app' OR public.is_support_session()
   OR (p_new->>'tenant_id')::UUID IS DISTINCT FROM public.current_tenant_id() THEN
  RETURN false;
 END IF;
 tenant:=(p_new->>'tenant_id')::UUID;
 v_sale:=(p_new->>CASE WHEN p_table='sale' THEN 'id'
   WHEN p_table='unit_stock_cart_command_v2' THEN 'checkout_sale_id' ELSE 'sale_id' END)::UUID;
 SELECT intent.* INTO i FROM public.pos_unit_payment_plan_intent intent
   JOIN public.pos_unit_payment_plan_binding binding
     ON binding.tenant_id=intent.tenant_id AND binding.id=intent.id
   WHERE intent.tenant_id=tenant AND intent.sale_id=v_sale
     AND NOT EXISTS(SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel cancel
       WHERE cancel.tenant_id=binding.tenant_id AND cancel.id=binding.id);
 IF i.id IS NULL OR i.cashier_user_id IS DISTINCT FROM public.current_app_user_id()
   OR NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=i.cashier_user_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     tenant,'pos.sell',i.branch_id),false) THEN RETURN false; END IF;
 IF p_table='pos_payment_attempt' THEN
  SELECT * INTO b FROM public.pos_unit_payment_attempt_binding
    WHERE tenant_id=tenant AND id=(p_new->>'id')::UUID AND intent_id=i.id;
  IF b.id IS NULL OR (p_new->>'sale_id',p_new->>'cashier_user_id',p_new->>'operation_id',
    p_new->>'payment_method',(p_new->>'amount')::NUMERIC,p_new->>'currency')
    IS DISTINCT FROM (i.sale_id::TEXT,i.cashier_user_id::TEXT,b.id::TEXT,
      b.method,b.amount,b.currency) OR p_new->>'evidence_required' IS DISTINCT FROM 'true'
    OR NOT EXISTS(SELECT 1 FROM public.sale s WHERE s.tenant_id=tenant AND s.id=v_sale
      AND s.status='draft' AND NOT s.is_test) THEN RETURN false; END IF;
  IF p_op='INSERT' THEN RETURN p_new->>'status'='pending'; END IF;
  IF p_op<>'UPDATE' OR p_old->>'id' IS DISTINCT FROM p_new->>'id'
    OR p_old->>'sale_id' IS DISTINCT FROM p_new->>'sale_id'
    OR p_old->>'tenant_id' IS DISTINCT FROM p_new->>'tenant_id' THEN RETURN false; END IF;
  IF p_new->>'status'='consumed' THEN
   RETURN EXISTS(SELECT 1 FROM public.pos_physical_checkout marker
     WHERE marker.tenant_id=tenant AND marker.sale_id=v_sale AND marker.payment_journal_version=3
       AND marker.payment_plan_intent_id=i.id AND (p_new->>'consumed_at')::TIMESTAMPTZ=
         (marker.result_payload->>'completed_at')::TIMESTAMPTZ);
  END IF;
  RETURN NOT EXISTS(SELECT 1 FROM public.pos_physical_checkout marker
    WHERE marker.tenant_id=tenant AND marker.sale_id=v_sale);
 ELSIF p_table='unit_stock_cart_command_v2' AND p_op='INSERT' THEN
  IF (p_new->>'id',p_new->>'branch_id',p_new->>'actor_id',p_new->>'currency',
      p_new->>'retail_version',p_new->>'mixed_checkout_sale_id') IS DISTINCT FROM
     (i.id::TEXT,i.branch_id::TEXT,i.cashier_user_id::TEXT,'TJS','1',NULL::TEXT)
    OR EXISTS(SELECT 1 FROM public.sale_payment p WHERE p.sale_id=v_sale)
    OR EXISTS(SELECT 1 FROM public.sale_item item WHERE item.sale_id=v_sale)
    OR (SELECT count(*) FROM public.pos_payment_attempt a WHERE a.sale_id=v_sale)
       <>(SELECT count(*) FROM jsonb_array_elements(i.request_payload->'payments') p
           WHERE p->>'method'<>'cash')
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(i.request_payload->'payments') p
      WHERE p->>'method'<>'cash' AND NOT EXISTS(
        SELECT 1 FROM public.pos_unit_payment_attempt_binding binding
        JOIN public.pos_payment_attempt a ON a.tenant_id=binding.tenant_id AND a.id=binding.id
        WHERE binding.tenant_id=tenant AND binding.intent_id=i.id
          AND binding.method=p->>'method' AND binding.amount=(p->>'amount')::NUMERIC
          AND a.sale_id=v_sale AND a.status='confirmed' AND a.evidence_required
          AND a.payment_method=binding.method AND a.amount=binding.amount)) THEN
   RETURN false;
  END IF;
  q:=public.unit_electronic_current_quote(tenant,v_sale,i.branch_id,
    i.register_id,i.shift_id,i.cashier_user_id);
  SELECT array_agg(p->>'method') INTO methods
    FROM jsonb_array_elements(i.request_payload->'payments') p;
  SELECT * INTO settings FROM public.tenant_settings WHERE tenant_id=tenant FOR SHARE;
  RETURN q=i.quote_payload AND settings.tenant_id IS NOT NULL
    AND settings.pos_payment_methods ?& methods
    AND (cardinality(methods)=1 OR settings.pos_mixed_payment_enabled)
    AND EXISTS(SELECT 1 FROM public.app_user WHERE id=i.cashier_user_id AND status='active')
    AND coalesce(public.tenant_actor_has_scoped_permission(tenant,'pos.sell',i.branch_id),false)
    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') p
      WHERE p->>'inventory_kind' IS DISTINCT FROM 'physical_unit');
 END IF;
 SELECT * INTO m FROM public.pos_physical_checkout
   WHERE tenant_id=tenant AND pos_physical_checkout.sale_id=v_sale AND payment_journal_version=3;
 IF m.id IS NULL OR m.payment_plan_intent_id IS DISTINCT FROM i.id THEN RETURN false; END IF;
 IF p_table='sale_payment' AND p_op='INSERT' THEN
  RETURN public.unit_electronic_payment_matches(tenant,v_sale,(p_new->>'id')::UUID,
    p_new->>'payment_method',(p_new->>'amount')::NUMERIC,p_new->>'currency',
    (p_new->>'payment_attempt_id')::UUID)
    AND p_new->>'operation_id'=p_new->>'id' AND p_new->>'operation_hash'=m.request_hash
    AND p_new->'metadata'='null'::JSONB;
 ELSIF p_table='sale' AND p_op='UPDATE' THEN
  RETURN p_old->>'status'='draft' AND p_new->>'status'='completed'
    AND p_new->>'receipt_number'=m.result_payload->>'receipt_number'
    AND p_new->'receipt_seq'=m.result_payload->'receipt_sequence'
    AND (p_new->>'completed_at')::TIMESTAMPTZ=(m.result_payload->>'completed_at')::TIMESTAMPTZ
    AND (p_new->>'total_amount')::NUMERIC=(m.result_payload->>'total_amount')::NUMERIC
    AND p_new->'receipt_snapshot'=m.receipt_payload;
 END IF;
 RETURN false;
END $$;
"""


def _clone_quote() -> None:
    signature = "unit_intent_current_quote(uuid,uuid,uuid,uuid,uuid,uuid)"
    definition = _definition(signature)
    definition = definition.replace(
        "public.unit_intent_current_quote(", "public.unit_electronic_current_quote(", 1
    )
    old = """OR EXISTS(SELECT 1 FROM public.pos_payment_attempt
      WHERE tenant_id=p_tenant AND sale_id=p_sale)"""
    new = """OR EXISTS(SELECT 1 FROM public.pos_payment_attempt a
      WHERE a.tenant_id=p_tenant AND a.sale_id=p_sale AND NOT EXISTS(
        SELECT 1 FROM public.pos_unit_payment_attempt_binding b
        JOIN public.pos_unit_payment_plan_binding plan
          ON plan.tenant_id=b.tenant_id AND plan.id=b.intent_id
        WHERE b.tenant_id=a.tenant_id AND b.id=a.id AND b.sale_id=a.sale_id
          AND b.cashier_user_id=a.cashier_user_id AND b.method=a.payment_method
          AND b.amount=a.amount AND b.currency=a.currency AND a.operation_id=b.id
          AND a.evidence_required AND NOT EXISTS(
            SELECT 1 FROM public.pos_unit_payment_plan_binding_cancel cancel
            WHERE cancel.tenant_id=plan.tenant_id AND cancel.id=plan.id)))"""
    if definition.count(old) != 1:
        raise RuntimeError("Historical neutral quote guard changed")
    op.get_bind().exec_driver_sql(definition.replace(old, new, 1))


def _clone_assert() -> None:
    definition = _definition("assert_physical_checkout(uuid,uuid,boolean)")
    changes = (
        ("public.assert_physical_checkout(", "public.assert_unit_electronic_checkout("),
        ("i public.pos_unit_cash_intent%ROWTYPE", "i public.pos_unit_payment_plan_intent%ROWTYPE"),
        ("   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt WHERE sale_id=p_sale)", ""),
        (
            "FROM public.pos_unit_cash_intent WHERE tenant_id=p_tenant AND id=m.cash_intent_id;",
            "FROM public.pos_unit_payment_plan_intent "
            "WHERE tenant_id=p_tenant AND id=m.payment_plan_intent_id;",
        ),
        ("i.expected_total_amount", "(i.quote_payload->>'total_amount')::NUMERIC"),
        (
            """ v_payment:=jsonb_build_object('id',m.payment_id,'method','cash',
   'amount',to_char(v_total,'FM999999999990.00'),'currency','TJS');""",
            " v_payment:=m.result_payload->'payments';",
        ),
        (
            "'lines',v_lines,'payments',jsonb_build_array(v_payment)",
            "'lines',v_lines,'payments',v_payment",
        ),
        (
            "'payments',jsonb_build_array(v_payment-ARRAY['id','currency'])",
            "'payments',(SELECT jsonb_agg(value-ARRAY['id','currency'] ORDER BY ordinality) "
            "FROM jsonb_array_elements(v_payment) WITH ORDINALITY)",
        ),
        (
            "(SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)<>1",
            "(SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)"
            "<>jsonb_array_length(v_payment)",
        ),
    )
    for old, new in changes:
        if definition.count(old) != 1:
            raise RuntimeError("Historical physical checkout assertion changed")
        definition = definition.replace(old, new, 1)
    start = definition.index(
        " IF EXISTS(SELECT 1 FROM public.sale_payment p WHERE p.sale_id=p_sale AND"
    )
    end = definition.index(" IF p_final AND", start)
    definition = (
        definition[:start]
        + " PERFORM public.assert_unit_electronic_payments(p_tenant,p_sale,p_final);\n"
        + definition[end:]
    )
    op.get_bind().exec_driver_sql(definition)


ASSERT_ANCHOR = (
    " SELECT * INTO m FROM public.pos_physical_checkout "
    "WHERE tenant_id=p_tenant AND sale_id=p_sale;\n"
)
ASSERT_DISPATCH = ASSERT_ANCHOR + """ IF m.payment_journal_version=3 THEN
  PERFORM public.assert_unit_electronic_checkout(p_tenant,p_sale,p_final);
  RETURN;
 END IF;
"""
STOCK_ANCHOR = " IF NEW.checkout_sale_id IS NULL THEN RETURN NEW; END IF;"
STOCK_DISPATCH = STOCK_ANCHOR + """
 IF coalesce(public.unit_electronic_write_allowed(
   'unit_stock_cart_command_v2',TG_OP,to_jsonb(NEW),NULL),false) THEN RETURN NEW; END IF;"""
FENCE_ANCHOR = "  IF public.unit_payment_binding_active(target.tenant,target.sale) THEN"
FENCE_DISPATCH = """  IF public.unit_payment_binding_active(target.tenant,target.sale)
    AND NOT coalesce(public.unit_electronic_write_allowed(TG_TABLE_NAME,TG_OP,
      CASE WHEN TG_OP='DELETE' THEN NULL ELSE to_jsonb(NEW) END,
      CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END),false) THEN"""
BOUNDARY_ANCHOR = "BEGIN\n IF TG_TABLE_NAME='sale' THEN"
BOUNDARY_DISPATCH = """BEGIN
 IF coalesce(public.unit_electronic_write_allowed(TG_TABLE_NAME,TG_OP,
   CASE WHEN TG_OP='DELETE' THEN NULL ELSE to_jsonb(NEW) END,
   CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END),false) THEN
  IF TG_TABLE_NAME<>'pos_payment_attempt' THEN
   PERFORM public.assert_unit_electronic_checkout(NEW.tenant_id,
     CASE WHEN TG_TABLE_NAME='sale' THEN NEW.id ELSE NEW.sale_id END,false);
  END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='sale' THEN"""
COMPONENT_ASSERT_ANCHOR = "BEGIN\n IF p_mixed THEN"
COMPONENT_ASSERT_DISPATCH = """BEGIN
 IF NOT p_mixed AND EXISTS(SELECT 1 FROM public.pos_physical_checkout
   WHERE tenant_id=p_tenant AND sale_id=p_sale AND payment_journal_version=3) THEN
  PERFORM public.assert_unit_electronic_payments(p_tenant,p_sale,true);
  RETURN;
 END IF;
 IF p_mixed THEN"""
COMPONENT_GUARD_ANCHOR = (
    " IF NEW.physical_checkout_id IS NOT NULL AND NEW.mixed_checkout_id IS NULL THEN"
)
COMPONENT_GUARD_DISPATCH = """ IF EXISTS(SELECT 1 FROM public.pos_physical_checkout
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id AND payment_journal_version=3) THEN
  IF NEW.mixed_checkout_id IS NOT NULL OR NOT EXISTS(
    SELECT 1 FROM public.pos_physical_checkout m WHERE m.tenant_id=NEW.tenant_id
      AND m.id=NEW.physical_checkout_id AND m.sale_id=NEW.sale_id)
    OR NOT public.unit_electronic_payment_matches(NEW.tenant_id,NEW.sale_id,NEW.id,
      NEW.method,NEW.amount,NEW.currency,NEW.payment_attempt_id)
    OR NOT EXISTS(SELECT 1 FROM public.sale_payment p
      WHERE p.tenant_id=NEW.tenant_id AND p.id=NEW.id
        AND (p.sale_id,p.payment_method,p.amount,p.currency,p.payment_attempt_id)
          IS NOT DISTINCT FROM
            (NEW.sale_id,NEW.method,NEW.amount,NEW.currency,NEW.payment_attempt_id))
  THEN
   RAISE EXCEPTION 'unit_checkout_payment_component_source_invalid' USING ERRCODE='23514';
  END IF;
  NEW.created_at:=clock_timestamp(); NEW.created_by:=public.current_app_user_id();
  RETURN NEW;
 END IF;
""" + COMPONENT_GUARD_ANCHOR
AUDIT_ANCHOR = "IF v_result IS NULL THEN RETURN NULL; END IF;"
AUDIT_DISPATCH = AUDIT_ANCHOR + """
      IF p_table_name='pos_unit_payment_attempt_binding' THEN
        RETURN jsonb_build_object('created_at',v_result->'created_at',
          'created_by',v_result->'created_by');
      END IF;"""
PATCHES = (
    ("assert_physical_checkout(uuid,uuid,boolean)", ASSERT_ANCHOR, ASSERT_DISPATCH),
    ("trg_physical_stock_checkout_binding()", STOCK_ANCHOR, STOCK_DISPATCH),
    ("trg_unit_payment_binding_fence()", FENCE_ANCHOR, FENCE_DISPATCH),
    ("trg_unit_cart_payment_boundary()", BOUNDARY_ANCHOR, BOUNDARY_DISPATCH),
    (
        "assert_unit_checkout_payment_components(uuid,uuid,boolean)",
        COMPONENT_ASSERT_ANCHOR,
        COMPONENT_ASSERT_DISPATCH,
    ),
    (
        "trg_unit_checkout_payment_component_guard()",
        COMPONENT_GUARD_ANCHOR,
        COMPONENT_GUARD_DISPATCH,
    ),
    ("audit_redact_table_snapshot(text,jsonb)", AUDIT_ANCHOR, AUDIT_DISPATCH),
)


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE unit_attempt_reference_grants AS
        SELECT oid::regclass::TEXT AS relation,
          has_table_privilege('aurum_schema_owner',oid,'REFERENCES') AS granted
        FROM pg_class WHERE oid IN ('public.pos_unit_payment_plan_intent'::regclass,
          'public.pos_payment_attempt'::regclass,'public.app_user'::regclass);
      GRANT REFERENCES ON public.pos_unit_payment_plan_intent,public.pos_payment_attempt,
        public.app_user TO aurum_schema_owner;
      ALTER TABLE public.pos_physical_checkout
        ALTER COLUMN cash_intent_id DROP NOT NULL,
        ADD COLUMN payment_plan_intent_id UUID,
        DROP CONSTRAINT pos_physical_checkout_check,
        DROP CONSTRAINT ck_physical_checkout_payment_journal_version,
        ADD CONSTRAINT ck_physical_checkout_payment_journal_version
          CHECK(payment_journal_version IN (1,2,3)),
        ADD CONSTRAINT ck_physical_checkout_payment_source CHECK(
          id=stock_command_id AND id=payment_id AND (
            (payment_journal_version IN (1,2) AND cash_intent_id IS NOT NULL
              AND cash_intent_id=id AND payment_plan_intent_id IS NULL)
            OR (payment_journal_version=3 AND cash_intent_id IS NULL
              AND payment_plan_intent_id IS NOT NULL AND payment_plan_intent_id=id))),
        ADD CONSTRAINT fk_physical_checkout_payment_plan
          FOREIGN KEY(tenant_id,payment_plan_intent_id)
          REFERENCES public.pos_unit_payment_plan_intent(tenant_id,id);
      CREATE TABLE public.pos_unit_payment_attempt_binding (
        tenant_id UUID NOT NULL,id UUID NOT NULL,intent_id UUID NOT NULL,sale_id UUID NOT NULL,
        branch_id UUID NOT NULL,cashier_user_id UUID NOT NULL REFERENCES public.app_user(id),
        method TEXT NOT NULL CHECK(method IN ('card','qr')),
        amount NUMERIC(14,2) NOT NULL CHECK(amount>0 AND amount<=999999999999.99),
        currency TEXT NOT NULL CHECK(currency='TJS'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID NOT NULL REFERENCES public.app_user(id),
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,intent_id,method),CHECK(id<>intent_id),
        CHECK(substring(id::TEXT,15,1)='4' AND substring(id::TEXT,20,1) IN ('8','9','a','b')),
        FOREIGN KEY(tenant_id,intent_id)
          REFERENCES public.pos_unit_payment_plan_intent(tenant_id,id),
        CONSTRAINT fk_unit_attempt_binding_attempt FOREIGN KEY(tenant_id,id)
          REFERENCES public.pos_payment_attempt(tenant_id,id) DEFERRABLE INITIALLY DEFERRED
      );
      DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT * FROM pg_temp.unit_attempt_reference_grants WHERE NOT granted LOOP
          EXECUTE format('REVOKE REFERENCES ON %s FROM aurum_schema_owner',r.relation);
        END LOOP;
      END $$;
      DROP TABLE pg_temp.unit_attempt_reference_grants;
      ALTER TABLE public.pos_unit_payment_attempt_binding OWNER TO aurum_schema_owner;
      ALTER TABLE public.pos_unit_payment_attempt_binding ENABLE ROW LEVEL SECURITY;
      ALTER TABLE public.pos_unit_payment_attempt_binding FORCE ROW LEVEL SECURITY;
      REVOKE ALL ON public.pos_unit_payment_attempt_binding FROM PUBLIC,aurum_app,aurum_support;
      GRANT SELECT,INSERT ON public.pos_unit_payment_attempt_binding TO aurum_app;
      GRANT SELECT ON public.pos_unit_payment_attempt_binding TO aurum_support;
      CREATE POLICY schema_owner ON public.pos_unit_payment_attempt_binding TO aurum_schema_owner
        USING(true) WITH CHECK(true);
      CREATE POLICY support ON public.pos_unit_payment_attempt_binding
        FOR SELECT TO aurum_support USING(true);
      CREATE POLICY tenant_actor ON public.pos_unit_payment_attempt_binding TO aurum_app USING(
        tenant_id=public.current_tenant_id() AND cashier_user_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id))
        WITH CHECK(tenant_id=public.current_tenant_id()
        AND cashier_user_id=public.current_app_user_id()
        AND public.tenant_actor_has_scoped_permission(tenant_id,'pos.sell',branch_id));
      CREATE INDEX ix_unit_attempt_binding_sale
        ON public.pos_unit_payment_attempt_binding(tenant_id,sale_id);
    """)
    _clone_quote()
    _sql(SQL)
    _clone_assert()
    for signature in FUNCTIONS:
        _sql(f"""
          ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;
          REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;
        """)
    for signature, old, new in PATCHES:
        _replace(signature, old, new, upgrade=True)
    _sql("""
      CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_payment_attempt_binding FOR EACH ROW
        EXECUTE FUNCTION public.trg_enforce_tenant_access('pos');
      CREATE TRIGGER a10_unit_attempt_binding_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.pos_unit_payment_attempt_binding FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_payment_attempt_binding_guard();
      CREATE CONSTRAINT TRIGGER unit_payment_attempt_binding_complete
        AFTER INSERT ON public.pos_unit_payment_attempt_binding
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_payment_attempt_binding_complete();
      CREATE CONSTRAINT TRIGGER unit_payment_attempt_binding_complete
        AFTER INSERT OR UPDATE ON public.pos_payment_attempt
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_unit_payment_attempt_binding_complete();
      CREATE TRIGGER z90_unit_attempt_binding_audit AFTER INSERT OR UPDATE OR DELETE
        ON public.pos_unit_payment_attempt_binding FOR EACH ROW
        EXECUTE FUNCTION public.trg_audit_log();
    """)


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_unit_payment_attempt_binding,public.pos_payment_attempt,
        public.pos_physical_checkout IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_unit_payment_attempt_binding)
          OR EXISTS(SELECT 1 FROM public.pos_physical_checkout WHERE payment_journal_version=3) THEN
          RAISE EXCEPTION 'unit_electronic_checkout_downgrade_requires_empty_history'
            USING ERRCODE='55000';
        END IF;
      END $$;
      DROP TRIGGER unit_payment_attempt_binding_complete ON public.pos_payment_attempt;
    """)
    for signature, old, new in reversed(PATCHES):
        _replace(signature, old, new, upgrade=False)
    _sql(f"DROP TABLE public.{TABLE};")
    for signature in reversed(FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    _sql("""
      ALTER TABLE public.pos_physical_checkout
        DROP CONSTRAINT fk_physical_checkout_payment_plan,
        DROP CONSTRAINT ck_physical_checkout_payment_source,
        DROP CONSTRAINT ck_physical_checkout_payment_journal_version,
        DROP COLUMN payment_plan_intent_id,
        ALTER COLUMN cash_intent_id SET NOT NULL,
        ADD CONSTRAINT ck_physical_checkout_payment_journal_version
          CHECK(payment_journal_version IN (1,2)),
        ADD CONSTRAINT pos_physical_checkout_check
          CHECK(id=cash_intent_id AND id=stock_command_id AND id=payment_id);
    """)
