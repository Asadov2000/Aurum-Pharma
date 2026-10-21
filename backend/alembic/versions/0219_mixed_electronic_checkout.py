"""Post bound mixed electronic sales without weakening historical cash guards.

Revision ID: 0219
Revises: 0218
"""

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from sqlalchemy import text

from alembic import op

revision: str = "0219"
down_revision: str | Sequence[str] | None = "0218"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW = "unit_electronic_checkout_marker"
NEW_FUNCTIONS = (
    "assert_mixed_electronic_checkout(uuid,uuid,boolean)",
    "assert_unit_electronic_checkout_source(uuid,uuid,boolean)",
)
GENERIC = (
    "unit_electronic_payment_matches(uuid,uuid,uuid,text,numeric,text,uuid)",
    "assert_unit_payment_attempt_binding(uuid,uuid)",
    "assert_unit_electronic_payments(uuid,uuid,boolean)",
    "unit_electronic_write_allowed(text,text,jsonb,jsonb)",
)


def _history(filename: str) -> ModuleType:
    spec = spec_from_file_location(
        "aurum_0219_" + filename.removesuffix(".py"), Path(__file__).with_name(filename)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load historical checkout migration")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(sql: str) -> None:
    _history("0171_unit_cash_intents.py")._sql(sql)


def _definition(signature: str) -> str:
    result = op.get_bind().scalar(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": "public." + signature},
    )
    if not isinstance(result, str):
        raise RuntimeError(f"Historical function missing: {signature}")
    return result


def _change(definition: str, old: str, new: str, *, count: int = 1) -> str:
    if definition.count(old) != count:
        raise RuntimeError("Mixed electronic historical guard changed")
    return definition.replace(old, new, count)


MARKERS_SQL = """
CREATE VIEW public.unit_electronic_checkout_marker WITH (security_barrier=true) AS
 SELECT m.tenant_id,m.id,m.sale_id,m.branch_id,m.register_id,m.shift_id,m.cashier_user_id,
   m.payment_plan_intent_id,m.payment_journal_version,m.request_hash,m.result_payload,
   m.receipt_payload,false AS mixed
 FROM public.pos_physical_checkout m WHERE m.payment_journal_version=3
   AND NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout other
     WHERE other.tenant_id=m.tenant_id AND other.sale_id=m.sale_id)
 UNION ALL
 SELECT m.tenant_id,m.id,m.sale_id,m.branch_id,m.register_id,m.shift_id,m.cashier_user_id,
   m.payment_plan_intent_id,m.payment_journal_version,m.request_hash,m.result_payload,
   m.receipt_payload,true AS mixed
 FROM public.pos_mixed_checkout m WHERE m.payment_journal_version=3
   AND NOT EXISTS(SELECT 1 FROM public.pos_physical_checkout other
     WHERE other.tenant_id=m.tenant_id AND other.sale_id=m.sale_id);
ALTER VIEW public.unit_electronic_checkout_marker OWNER TO aurum_schema_owner;
REVOKE ALL ON public.unit_electronic_checkout_marker FROM PUBLIC,aurum_app,aurum_support;
"""

# Every replacement is reversible and checked against exactly one historical body.
STOCK_OLD = """  IF (p_new->>'id',p_new->>'branch_id',p_new->>'actor_id',p_new->>'currency',
      p_new->>'retail_version',p_new->>'mixed_checkout_sale_id') IS DISTINCT FROM
     (i.id::TEXT,i.branch_id::TEXT,i.cashier_user_id::TEXT,'TJS','1',NULL::TEXT)
    OR EXISTS(SELECT 1 FROM public.sale_payment p WHERE p.sale_id=v_sale)
    OR EXISTS(SELECT 1 FROM public.sale_item item WHERE item.sale_id=v_sale)"""
STOCK_NEW = """  IF (p_new->>'id',p_new->>'branch_id',p_new->>'actor_id',p_new->>'currency',
      p_new->>'retail_version') IS DISTINCT FROM
     (i.id::TEXT,i.branch_id::TEXT,i.cashier_user_id::TEXT,'TJS','1')
    OR CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(i.quote_payload->'lines') line
         WHERE line->>'inventory_kind'='legacy_pack') THEN
       p_new->>'mixed_checkout_sale_id' IS DISTINCT FROM i.sale_id::TEXT
       OR p_new->>'checkout_sale_id' IS NOT NULL
       OR NOT EXISTS(SELECT 1 FROM public.sale_item item WHERE item.sale_id=v_sale)
     ELSE p_new->>'checkout_sale_id' IS DISTINCT FROM i.sale_id::TEXT
       OR p_new->>'mixed_checkout_sale_id' IS NOT NULL
       OR EXISTS(SELECT 1 FROM public.sale_item item WHERE item.sale_id=v_sale) END
    OR EXISTS(SELECT 1 FROM public.sale_payment p WHERE p.sale_id=v_sale)"""
KIND_OLD = """    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') p
      WHERE p->>'inventory_kind' IS DISTINCT FROM 'physical_unit');"""
KIND_NEW = """    AND EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') p
      WHERE p->>'inventory_kind'='physical_unit')
    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') p
      WHERE p->>'inventory_kind' NOT IN ('legacy_pack','physical_unit'));"""
PAYMENT_PARENT_OLD = (
    """       c.physical_checkout_id IS DISTINCT FROM m.id OR c.mixed_checkout_id IS NOT NULL"""
)
PAYMENT_PARENT_NEW = """       (c.physical_checkout_id,c.mixed_checkout_id) IS DISTINCT FROM
         (CASE WHEN m.mixed THEN NULL::UUID ELSE m.id END,
          CASE WHEN m.mixed THEN m.id ELSE NULL::UUID END)"""
WRITE_PARENT_OLD = """ v_sale:=(p_new->>CASE WHEN p_table='sale' THEN 'id'
   WHEN p_table='unit_stock_cart_command_v2' THEN 'checkout_sale_id' ELSE 'sale_id' END)::UUID;"""
WRITE_PARENT_NEW = """ v_sale:=CASE WHEN p_table='unit_stock_cart_command_v2' THEN
   coalesce((p_new->>'checkout_sale_id')::UUID,(p_new->>'mixed_checkout_sale_id')::UUID)
   ELSE (p_new->>CASE WHEN p_table='sale' THEN 'id' ELSE 'sale_id' END)::UUID END;"""


def _generic_definition(signature: str, definition: str, *, upgrade: bool) -> str:
    if upgrade:
        before, after = "public.pos_physical_checkout", "public." + VIEW
        count = definition.count(before)
        if count < 1:
            raise RuntimeError("Electronic helper marker source missing")
        definition = _change(definition, before, after, count=count)
    changes: list[tuple[str, str]] = []
    if signature.startswith("assert_unit_electronic_payments("):
        changes.append((PAYMENT_PARENT_OLD, PAYMENT_PARENT_NEW))
    if signature.startswith("unit_electronic_write_allowed("):
        changes.extend(
            ((STOCK_OLD, STOCK_NEW), (KIND_OLD, KIND_NEW), (WRITE_PARENT_OLD, WRITE_PARENT_NEW))
        )
        changes.append(("pos_physical_checkout.sale_id", "unit_electronic_checkout_marker.sale_id"))
        changes.append(
            (
                """  RETURN NOT EXISTS(SELECT 1 FROM public.unit_electronic_checkout_marker marker
    WHERE marker.tenant_id=tenant AND marker.sale_id=v_sale);""",
                """  RETURN NOT EXISTS(SELECT 1 FROM public.pos_physical_checkout marker
    WHERE marker.tenant_id=tenant AND marker.sale_id=v_sale)
    AND NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout marker
      WHERE marker.tenant_id=tenant AND marker.sale_id=v_sale);""",
            )
        )
    if not upgrade:
        changes.reverse()
    for old, new in changes:
        definition = _change(definition, old if upgrade else new, new if upgrade else old)
    # These four private helpers intentionally inspect every marker lookup through
    # the same exclusive source projection. No public table permissions change.
    if upgrade:
        return definition
    before = "public." + VIEW
    after = "public.pos_physical_checkout"
    count = definition.count(before)
    if count < 1:
        raise RuntimeError("Electronic helper marker source missing")
    return _change(definition, before, after, count=count)


ATTEMPT_KIND_OLD = """   OR EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') p
     WHERE p->>'inventory_kind' IS DISTINCT FROM 'physical_unit')"""
ATTEMPT_KIND_NEW = """   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') p
     WHERE p->>'inventory_kind'='physical_unit')
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(q->'lines') p
     WHERE p->>'inventory_kind' NOT IN ('physical_unit','legacy_pack'))"""
RX_ANCHOR = """ IF NOT EXISTS(SELECT 1 FROM public.app_user WHERE id=NEW.cashier_user_id AND status='active')
   OR NOT coalesce(public.tenant_actor_has_scoped_permission(
     NEW.tenant_id,'pos.sell',NEW.branch_id),false) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_actor_denied' USING ERRCODE='42501';
 END IF;
 NEW.created_at"""
RX_DISPATCH = """ IF EXISTS(SELECT 1 FROM public.sale_item item
   JOIN public.tenant_catalog catalog
     ON catalog.tenant_id=item.tenant_id AND catalog.id=item.catalog_id
   WHERE item.tenant_id=i.tenant_id AND item.sale_id=i.sale_id
     AND (catalog.dispensing_type='special' OR NOT catalog.is_active
       OR catalog.deleted_at IS NOT NULL OR catalog.currency<>'TJS'
       OR (catalog.dispensing_type='prescription' AND NOT EXISTS(
         SELECT 1 FROM public.prescription_log p
         WHERE p.tenant_id=item.tenant_id AND p.sale_id=item.sale_id
           AND (p.sale_item_id=item.id OR
             (p.unit_cart_item_id IS NULL AND p.sale_item_id IS NULL))
           AND regexp_replace(concat_ws('',p.prescription_number,p.doctor_name,p.doctor_license,
             p.patient_name,p.notes),'[[:space:]]','','g')<>'')))) THEN
  RAISE EXCEPTION 'unit_payment_attempt_binding_prescription_required' USING ERRCODE='23514';
 END IF;
""" + RX_ANCHOR
FENCE_PARENT_OLD = """  parent_id:=(payload->>CASE WHEN TG_TABLE_NAME='sale' THEN 'id'
    WHEN TG_TABLE_NAME='unit_stock_cart_command_v2' THEN 'checkout_sale_id'
    ELSE 'sale_id' END)::UUID;"""
FENCE_PARENT_NEW = """  parent_id:=CASE WHEN TG_TABLE_NAME='unit_stock_cart_command_v2' THEN
    coalesce((payload->>'checkout_sale_id')::UUID,(payload->>'mixed_checkout_sale_id')::UUID)
    ELSE (payload->>CASE WHEN TG_TABLE_NAME='sale' THEN 'id' ELSE 'sale_id' END)::UUID END;"""
MIXED_ASSERT_ANCHOR = (
    " SELECT * INTO m FROM public.pos_mixed_checkout WHERE tenant_id=p_tenant AND sale_id=p_sale;\n"
)
MIXED_ASSERT_DISPATCH = MIXED_ASSERT_ANCHOR + """ IF m.payment_journal_version=3 THEN
  PERFORM public.assert_mixed_electronic_checkout(p_tenant,p_sale,p_final);
  RETURN;
 END IF;
"""
MIXED_STOCK_ANCHOR = " IF NEW.mixed_checkout_sale_id IS NULL THEN RETURN NEW; END IF;"
MIXED_STOCK_DISPATCH = MIXED_STOCK_ANCHOR + """
 IF coalesce(public.unit_electronic_write_allowed(
   'unit_stock_cart_command_v2',TG_OP,to_jsonb(NEW),NULL),false) THEN RETURN NEW; END IF;"""
COMPONENT_ASSERT_ANCHOR = (
    "BEGIN\n IF NOT p_mixed AND EXISTS(SELECT 1 FROM public.pos_physical_checkout"
)
COMPONENT_ASSERT_DISPATCH = """BEGIN
 IF p_mixed AND EXISTS(SELECT 1 FROM public.pos_mixed_checkout
   WHERE tenant_id=p_tenant AND sale_id=p_sale AND payment_journal_version=3) THEN
  PERFORM public.assert_unit_electronic_payments(p_tenant,p_sale,true);
  RETURN;
 END IF;
 IF NOT p_mixed AND EXISTS(SELECT 1 FROM public.pos_physical_checkout"""
COMPONENT_GUARD_ANCHOR = (
    " IF NEW.physical_checkout_id IS NOT NULL AND NEW.mixed_checkout_id IS NULL THEN"
)
COMPONENT_GUARD_DISPATCH = """ IF EXISTS(SELECT 1 FROM public.pos_mixed_checkout
   WHERE tenant_id=NEW.tenant_id AND sale_id=NEW.sale_id AND payment_journal_version=3) THEN
  IF NEW.physical_checkout_id IS NOT NULL OR NOT EXISTS(
    SELECT 1 FROM public.pos_mixed_checkout m WHERE m.tenant_id=NEW.tenant_id
      AND m.id=NEW.mixed_checkout_id AND m.sale_id=NEW.sale_id)
    OR NOT public.unit_electronic_payment_matches(NEW.tenant_id,NEW.sale_id,NEW.id,
      NEW.method,NEW.amount,NEW.currency,NEW.payment_attempt_id)
    OR NOT EXISTS(SELECT 1 FROM public.sale_payment p
      WHERE p.tenant_id=NEW.tenant_id AND p.id=NEW.id
        AND (p.sale_id,p.payment_method,p.amount,p.currency,p.payment_attempt_id)
          IS NOT DISTINCT FROM
            (NEW.sale_id,NEW.method,NEW.amount,NEW.currency,NEW.payment_attempt_id)) THEN
   RAISE EXCEPTION 'unit_checkout_payment_component_source_invalid' USING ERRCODE='23514';
  END IF;
  NEW.created_at:=clock_timestamp(); NEW.created_by:=public.current_app_user_id();
  RETURN NEW;
 END IF;
""" + COMPONENT_GUARD_ANCHOR
PATCHES = (
    ("trg_unit_payment_attempt_binding_guard()", ATTEMPT_KIND_OLD, ATTEMPT_KIND_NEW, 1),
    ("trg_unit_payment_attempt_binding_guard()", RX_ANCHOR, RX_DISPATCH, 1),
    ("trg_unit_payment_binding_fence()", FENCE_PARENT_OLD, FENCE_PARENT_NEW, 2),
    ("assert_mixed_checkout(uuid,uuid,boolean)", MIXED_ASSERT_ANCHOR, MIXED_ASSERT_DISPATCH, 1),
    ("trg_mixed_stock_checkout_binding()", MIXED_STOCK_ANCHOR, MIXED_STOCK_DISPATCH, 1),
    (
        "trg_mixed_stock_checkout_binding()",
        " IF current_user<>'aurum_app' OR public.is_support_session()",
        " IF session_user<>'aurum_app' OR public.is_support_session()",
        1,
    ),
    (
        "assert_unit_checkout_payment_components(uuid,uuid,boolean)",
        COMPONENT_ASSERT_ANCHOR,
        COMPONENT_ASSERT_DISPATCH,
        1,
    ),
    (
        "trg_unit_checkout_payment_component_guard()",
        COMPONENT_GUARD_ANCHOR,
        COMPONENT_GUARD_DISPATCH,
        1,
    ),
    (
        "trg_unit_cart_payment_boundary()",
        "PERFORM public.assert_unit_electronic_checkout(",
        "PERFORM public.assert_unit_electronic_checkout_source(",
        2,
    ),
)


def _clone_assert(definition: str) -> str:
    changes = (
        ("public.assert_mixed_checkout(", "public.assert_mixed_electronic_checkout("),
        ("i public.pos_unit_cash_intent%ROWTYPE", "i public.pos_unit_payment_plan_intent%ROWTYPE"),
        ("   OR EXISTS(SELECT 1 FROM public.pos_payment_attempt WHERE sale_id=p_sale)", ""),
        (
            "FROM public.pos_unit_cash_intent WHERE tenant_id=p_tenant AND id=m.cash_intent_id;",
            "FROM public.pos_unit_payment_plan_intent WHERE tenant_id=p_tenant AND id=m.payment_plan_intent_id;",
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
            "'payments',(SELECT jsonb_agg(value-ARRAY['id','currency'] ORDER BY ordinality) FROM jsonb_array_elements(v_payment) WITH ORDINALITY)",
        ),
        (
            "(SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)<>1",
            "(SELECT count(*) FROM public.sale_payment WHERE sale_id=p_sale)<>jsonb_array_length(v_payment)",
        ),
    )
    for old, new in changes:
        definition = _change(definition, old, new)
    start = definition.index(
        " IF EXISTS(SELECT 1 FROM public.sale_payment p WHERE p.sale_id=p_sale AND"
    )
    end = definition.index(" IF p_final AND", start)
    return (
        definition[:start]
        + " PERFORM public.assert_unit_electronic_payments(p_tenant,p_sale,p_final);\n"
        + definition[end:]
    )


SOURCE_SQL = """
CREATE FUNCTION public.assert_unit_electronic_checkout_source(
 p_tenant UUID,p_sale UUID,p_final BOOLEAN) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.pos_mixed_checkout
   WHERE tenant_id=p_tenant AND sale_id=p_sale AND payment_journal_version=3) THEN
  PERFORM public.assert_mixed_electronic_checkout(p_tenant,p_sale,p_final);
 ELSE
  PERFORM public.assert_unit_electronic_checkout(p_tenant,p_sale,p_final);
 END IF;
END $$;
"""


def upgrade() -> None:
    _sql("""
      CREATE TEMP TABLE mixed_electronic_reference_grants AS
        SELECT has_table_privilege('aurum_schema_owner',
          'public.pos_unit_payment_plan_intent','REFERENCES') AS granted;
      GRANT REFERENCES ON public.pos_unit_payment_plan_intent TO aurum_schema_owner;
      ALTER TABLE public.pos_mixed_checkout
        ALTER COLUMN cash_intent_id DROP NOT NULL,
        ADD COLUMN payment_plan_intent_id UUID,
        DROP CONSTRAINT pos_mixed_checkout_check,
        DROP CONSTRAINT ck_mixed_checkout_payment_journal_version,
        ADD CONSTRAINT ck_mixed_checkout_payment_journal_version CHECK(payment_journal_version IN (1,2,3)),
        ADD CONSTRAINT ck_mixed_checkout_payment_source CHECK(
          id=stock_command_id AND id=payment_id AND (
            (payment_journal_version IN (1,2) AND cash_intent_id IS NOT NULL
              AND cash_intent_id=id AND payment_plan_intent_id IS NULL)
            OR (payment_journal_version=3 AND cash_intent_id IS NULL
              AND payment_plan_intent_id IS NOT NULL AND payment_plan_intent_id=id))),
        ADD CONSTRAINT fk_mixed_checkout_payment_plan FOREIGN KEY(tenant_id,payment_plan_intent_id)
          REFERENCES public.pos_unit_payment_plan_intent(tenant_id,id);
      DO $$ BEGIN
        IF NOT (SELECT granted FROM pg_temp.mixed_electronic_reference_grants) THEN
          REVOKE REFERENCES ON public.pos_unit_payment_plan_intent FROM aurum_schema_owner;
        END IF;
      END $$;
      DROP TABLE pg_temp.mixed_electronic_reference_grants;
    """)
    _sql(MARKERS_SQL)
    for signature in GENERIC:
        op.get_bind().exec_driver_sql(
            _generic_definition(signature, _definition(signature), upgrade=True)
        )
    op.get_bind().exec_driver_sql(
        _clone_assert(_definition("assert_mixed_checkout(uuid,uuid,boolean)"))
    )
    _sql(SOURCE_SQL)
    for signature in NEW_FUNCTIONS:
        _sql(
            f"ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner; REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;"
        )
    for signature, old, new, count in PATCHES:
        op.get_bind().exec_driver_sql(_change(_definition(signature), old, new, count=count))
    _sql("ALTER FUNCTION public.trg_mixed_stock_checkout_binding() SECURITY DEFINER;")


def downgrade() -> None:
    _sql("""
      LOCK TABLE public.pos_mixed_checkout,public.pos_unit_payment_attempt_binding,
        public.pos_unit_payment_plan_intent IN ACCESS EXCLUSIVE MODE;
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.pos_mixed_checkout WHERE payment_journal_version=3)
          OR EXISTS(SELECT 1 FROM public.pos_unit_payment_attempt_binding b
            JOIN public.pos_unit_payment_plan_intent i ON i.tenant_id=b.tenant_id AND i.id=b.intent_id
            WHERE EXISTS(SELECT 1 FROM jsonb_array_elements(i.quote_payload->'lines') line
              WHERE line->>'inventory_kind'='legacy_pack')) THEN
          RAISE EXCEPTION 'mixed_electronic_checkout_downgrade_requires_empty_history' USING ERRCODE='55000';
        END IF;
      END $$;
    """)
    for signature, old, new, count in reversed(PATCHES):
        op.get_bind().exec_driver_sql(_change(_definition(signature), new, old, count=count))
    _sql("ALTER FUNCTION public.trg_mixed_stock_checkout_binding() SECURITY INVOKER;")
    for signature in reversed(NEW_FUNCTIONS):
        _sql(f"DROP FUNCTION public.{signature};")
    for signature in GENERIC:
        op.get_bind().exec_driver_sql(
            _generic_definition(signature, _definition(signature), upgrade=False)
        )
    _sql("""
      DROP VIEW public.unit_electronic_checkout_marker;
      ALTER TABLE public.pos_mixed_checkout
        DROP CONSTRAINT fk_mixed_checkout_payment_plan,
        DROP CONSTRAINT ck_mixed_checkout_payment_source,
        DROP CONSTRAINT ck_mixed_checkout_payment_journal_version,
        DROP COLUMN payment_plan_intent_id,
        ALTER COLUMN cash_intent_id SET NOT NULL,
        ADD CONSTRAINT ck_mixed_checkout_payment_journal_version CHECK(payment_journal_version IN (1,2)),
        ADD CONSTRAINT pos_mixed_checkout_check CHECK(id=cash_intent_id AND id=stock_command_id AND id=payment_id);
    """)
