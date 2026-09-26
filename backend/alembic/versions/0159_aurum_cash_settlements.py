"""Independent, four-eyes cash settlements; no subscription/access mutation.

Revision ID: 0159
Revises: 0158
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0159"
down_revision = "0158"
branch_labels = None
depends_on = None


def _execute(sql: str) -> None:
    """Send one statement at a time; asyncpg disallows multi-statement prepares."""
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.execute(statement)
            start = match.end()
    if sql[start:].strip():
        op.execute(sql[start:])


def _source(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"cash_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMAND_SIGNATURE = (
    "public.command_billing_cash_payment(UUID,UUID,UUID,TEXT,UUID,TEXT,"
    "UUID,INTEGER,NUMERIC,TIMESTAMPTZ,TEXT,UUID,TEXT)"
)
READ_SIGNATURE = "public.read_billing_cash_payments(UUID,UUID,UUID,UUID,BOOLEAN,INTEGER,INTEGER)"

COMMAND_SQL = r"""
CREATE FUNCTION public.command_billing_cash_payment(
 p_actor UUID,p_session UUID,p_operation UUID,p_hash TEXT,p_tenant UUID,p_action TEXT,
 p_document UUID,p_version INTEGER,p_amount NUMERIC,p_paid_at TIMESTAMPTZ,
 p_purpose TEXT,p_invoice UUID,p_reason TEXT
) RETURNS TABLE(result JSONB,applied BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_mfa TIMESTAMPTZ; v_payload JSONB; v_existing public.billing_financial_operation%ROWTYPE;
 v_doc public.billing_cash_document%ROWTYPE; v_result JSONB; v_receipt JSONB;
 v_payment UUID; v_entry UUID; v_allocated NUMERIC(14,2):=0; v_credit NUMERIC(14,2);
 v_due NUMERIC(14,2); v_invoice_number TEXT; v_tenant_name TEXT; v_number TEXT;
 v_now TIMESTAMPTZ:=statement_timestamp();
BEGIN
 IF p_action IS NULL OR p_action NOT IN ('create','approve','reject') THEN
   RAISE EXCEPTION 'Invalid cash command' USING ERRCODE='22023';
 END IF;
 v_mfa:=public.assert_and_lock_platform_recent_capability(p_actor,p_session,
   CASE WHEN p_action='create' THEN 'platform.billing.payment.review'
   ELSE 'platform.billing.payment.approve' END);
 IF p_operation IS NULL OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
   OR p_tenant IS NULL THEN
   RAISE EXCEPTION 'Invalid cash command identity' USING ERRCODE='22023';
 END IF;
 IF p_action='create' THEN
   IF p_document IS NOT NULL OR p_version IS NOT NULL OR p_reason IS NOT NULL
     OR p_amount IS NULL OR p_amount::TEXT IN ('NaN','Infinity','-Infinity')
     OR p_amount<=0 OR p_amount>999999999999.99 OR p_amount<>round(p_amount,2)
     OR p_paid_at IS NULL OR NOT isfinite(p_paid_at)
     OR p_paid_at>v_now+INTERVAL '5 minutes' OR p_paid_at<v_now-INTERVAL '366 days'
     OR p_purpose IS NULL OR length(btrim(p_purpose)) NOT BETWEEN 3 AND 500 THEN
     RAISE EXCEPTION 'Invalid cash amount, date or purpose' USING ERRCODE='22023';
   END IF;
 ELSE
   IF p_document IS NULL OR p_version IS NULL OR p_version<1
     OR p_amount IS NOT NULL OR p_paid_at IS NOT NULL OR p_purpose IS NOT NULL
     OR p_invoice IS NOT NULL OR (p_action='approve' AND p_reason IS NOT NULL)
     OR (p_action='reject' AND (p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 3 AND
       500)) THEN
     RAISE EXCEPTION 'Invalid cash decision' USING ERRCODE='22023';
   END IF;
 END IF;
 v_payload:=jsonb_build_object('tenant_id',p_tenant,'action',p_action,
   'document_id',p_document,'expected_row_version',p_version,'amount',p_amount,
   'paid_at',p_paid_at,'purpose',p_purpose,'target_invoice_id',p_invoice,'reason',p_reason);
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::TEXT,9501));
 SELECT * INTO v_existing FROM public.billing_financial_operation WHERE operation_id=p_operation;
 IF FOUND THEN
   IF v_existing.operation_type<>'cash_payment_'||p_action OR v_existing.actor_user_id<>p_actor
     OR v_existing.request_hash<>p_hash OR v_existing.request_payload<>v_payload THEN
     RAISE EXCEPTION 'Billing operation id was reused' USING ERRCODE='23505';
   END IF;
   RETURN QUERY SELECT v_existing.result_snapshot,false; RETURN;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_tenant::TEXT,9701));
 SELECT name INTO v_tenant_name FROM public.tenant WHERE id=p_tenant;
 IF NOT FOUND THEN RAISE EXCEPTION 'Tenant not found' USING ERRCODE='P0002'; END IF;
 IF p_action='create' THEN
   IF p_invoice IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.billing_invoice
      WHERE tenant_id=p_tenant AND id=p_invoice AND document_state='issued') THEN
     RAISE EXCEPTION 'Invoice not found' USING ERRCODE='P0002';
   END IF;
   v_number:=nextval('public.billing_cash_document_number_seq')::TEXT;
   INSERT INTO public.billing_cash_document(tenant_id,document_number,amount,paid_at,purpose,
     target_invoice_id,created_by,created_session_id,created_mfa_at,operation_id)
   VALUES(p_tenant,'AC-'||to_char(v_now AT TIME ZONE 'UTC','YYYY')||'-'||
     lpad(v_number,greatest(8,length(v_number)),'0'),
     p_amount,p_paid_at,btrim(p_purpose),p_invoice,p_actor,p_session,v_mfa,p_operation)
   RETURNING * INTO v_doc;
 ELSE
   SELECT * INTO v_doc FROM public.billing_cash_document
     WHERE tenant_id=p_tenant AND id=p_document FOR UPDATE;
   IF NOT FOUND THEN RAISE EXCEPTION 'Cash document not found' USING ERRCODE='P0002'; END IF;
   IF v_doc.status<>'pending_approval' OR v_doc.row_version<>p_version THEN
     RAISE EXCEPTION 'Cash document changed; reload it' USING ERRCODE='40001';
   END IF;
   IF v_doc.created_by=p_actor THEN
     RAISE EXCEPTION 'Another Aurum employee must confirm or reject cash' USING ERRCODE='22023';
   END IF;
   IF p_action='approve' THEN
     IF v_doc.target_invoice_id IS NOT NULL THEN
       SELECT invoice_number INTO v_invoice_number FROM public.billing_invoice
         WHERE tenant_id=p_tenant AND id=v_doc.target_invoice_id AND document_state='issued' FOR
           UPDATE;
       IF NOT FOUND THEN RAISE EXCEPTION 'Invoice not found' USING ERRCODE='P0002'; END IF;
       SELECT COALESCE(sum(CASE side WHEN 'debit' THEN amount ELSE -amount END),0)
         INTO v_due FROM public.billing_journal_posting
         WHERE tenant_id=p_tenant AND invoice_id=v_doc.target_invoice_id
           AND account_code='accounts_receivable';
       v_allocated:=least(v_doc.amount,greatest(v_due,0));
     END IF;
     v_credit:=v_doc.amount-v_allocated;
     IF v_credit+(SELECT COALESCE(sum(CASE side WHEN 'credit' THEN amount ELSE -amount END),0)
       FROM public.billing_journal_posting WHERE tenant_id=p_tenant
         AND account_code='tenant_credit')
       >999999999999.99 THEN
       RAISE EXCEPTION 'Cash advance exceeds the account balance limit' USING ERRCODE='22023';
     END IF;
     INSERT INTO public.billing_payment(tenant_id,cash_document_id,payment_method,operation_id,
       amount,currency,paid_at,lifecycle_state,confirmed_by,confirmed_session_id,mfa_verified_at)
     VALUES(p_tenant,v_doc.id,'cash',p_operation,v_doc.amount,'TJS',v_doc.paid_at,
       'confirmed',p_actor,p_session,v_mfa) RETURNING id INTO v_payment;
     INSERT INTO public.billing_journal_entry(tenant_id,operation_id,entry_sequence,entry_type,
       currency,actor_user_id,actor_session_id)
     VALUES(p_tenant,p_operation,1,'payment_confirmed','TJS',p_actor,p_session) RETURNING id
       INTO v_entry;
     INSERT INTO public.billing_journal_posting(tenant_id,entry_id,posting_sequence,account_code,
       side,amount,payment_id) VALUES
       (p_tenant,v_entry,1,'cash_on_hand','debit',v_doc.amount,v_payment),
       (p_tenant,v_entry,2,'unapplied_cash','credit',v_doc.amount,v_payment);
     PERFORM public.assert_billing_journal_entry_balanced(v_entry);
     IF v_allocated>0 THEN
       INSERT INTO public.billing_payment_allocation(tenant_id,payment_id,invoice_id,
         allocation_order,amount)
       VALUES(p_tenant,v_payment,v_doc.target_invoice_id,1,v_allocated);
       INSERT INTO public.billing_journal_entry(tenant_id,operation_id,entry_sequence,entry_type,
         currency,actor_user_id,actor_session_id)
       VALUES(p_tenant,p_operation,2,'payment_allocated','TJS',p_actor,p_session) RETURNING id
         INTO v_entry;
       INSERT INTO public.billing_journal_posting(tenant_id,entry_id,posting_sequence,account_code,
         side,amount,payment_id,invoice_id) VALUES
         (p_tenant,v_entry,1,'unapplied_cash','debit',v_allocated,v_payment,
           v_doc.target_invoice_id),
         (p_tenant,v_entry,2,'accounts_receivable','credit',v_allocated,v_payment,
           v_doc.target_invoice_id);
       PERFORM public.assert_billing_journal_entry_balanced(v_entry);
     END IF;
     IF v_credit>0 THEN
       INSERT INTO public.billing_tenant_credit(tenant_id,payment_id,amount) VALUES(p_tenant,
         v_payment,v_credit);
       INSERT INTO public.billing_journal_entry(tenant_id,operation_id,entry_sequence,entry_type,
         currency,actor_user_id,actor_session_id)
       VALUES(p_tenant,p_operation,3,'credit_created','TJS',p_actor,p_session) RETURNING id INTO
         v_entry;
       INSERT INTO public.billing_journal_posting(tenant_id,entry_id,posting_sequence,account_code,
         side,amount,payment_id) VALUES
         (p_tenant,v_entry,1,'unapplied_cash','debit',v_credit,v_payment),
         (p_tenant,v_entry,2,'tenant_credit','credit',v_credit,v_payment);
       PERFORM public.assert_billing_journal_entry_balanced(v_entry);
     END IF;
     v_receipt:=jsonb_build_object('id',v_doc.id,'document_number',v_doc.document_number,
       'title','Подтверждение приёма наличных','tenant_id',p_tenant,'tenant_name',v_tenant_name,
       'purpose',v_doc.purpose,'amount',v_doc.amount::TEXT,'currency','TJS','paid_at',v_doc.paid_at,
       'confirmed_at',v_now,'allocated_amount',v_allocated::TEXT,'credit_amount',v_credit::TEXT,
       'allocations',CASE WHEN v_allocated>0 THEN jsonb_build_array(jsonb_build_object(
         'invoice_id',v_doc.target_invoice_id,'invoice_number',v_invoice_number,'amount',
           v_allocated::TEXT))
         ELSE '[]'::JSONB END);
   END IF;
   UPDATE public.billing_cash_document SET status=CASE WHEN p_action='approve' THEN 'confirmed'
     ELSE 'rejected' END,
     row_version=row_version+1,decided_by=p_actor,decided_session_id=p_session,decided_mfa_at=v_mfa,
     decided_at=v_now,decision_operation_id=p_operation,rejection_reason=p_reason,
     receipt_snapshot=v_receipt,allocated_amount=v_allocated,credit_amount=COALESCE(v_credit,0)
     WHERE id=v_doc.id RETURNING * INTO v_doc;
 END IF;
 v_result:=public.billing_cash_document_json(v_doc,p_actor);
 INSERT INTO public.billing_financial_operation(operation_id,operation_type,tenant_id,actor_user_id,
   actor_session_id,mfa_verified_at,request_hash,request_payload,result_snapshot)
 VALUES(p_operation,'cash_payment_'||p_action,p_tenant,p_actor,p_session,v_mfa,p_hash,v_payload,
   v_result);
 INSERT INTO public.billing_outbox_event(tenant_id,operation_id,event_type,aggregate_type,
   aggregate_id,payload)
 VALUES(p_tenant,p_operation,'billing.cash_payment.'||p_action,'billing_cash_document',v_doc.id,
   jsonb_build_object('cash_document_id',v_doc.id,'status',v_doc.status));
 RETURN QUERY SELECT v_result,true;
END $$
"""

READ_SQL = r"""
CREATE FUNCTION public.read_billing_cash_payments(p_actor UUID,p_session UUID,p_tenant UUID,
 p_document UUID,p_receipt BOOLEAN,p_limit INTEGER,p_offset INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_platform BOOLEAN:=SESSION_USER='aurum_support'; v_result JSONB;
BEGIN
 IF v_platform THEN
   PERFORM public.assert_and_lock_platform_recent_capability(p_actor,p_session,
     'platform.billing.view');
 ELSE
   IF SESSION_USER<>'aurum_app' OR public.is_support_session() OR public.is_tenant_support_session()
     OR COALESCE(current_setting('app.support_session',true),'')='true'
     OR p_actor IS NULL OR p_actor IS DISTINCT FROM public.current_app_user_id()
     OR p_tenant IS NULL OR p_tenant IS DISTINCT FROM public.current_tenant_id()
     OR NOT public.tenant_actor_has_permission(p_tenant,'billing.overview.view')
     OR NOT public.tenant_actor_has_permission(p_tenant,'billing.invoice.view') THEN
     RAISE EXCEPTION 'Tenant cash documents unavailable' USING ERRCODE='42501';
   END IF;
 END IF;
 IF p_tenant IS NULL OR p_receipt IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
   OR p_offset IS NULL OR p_offset<0 THEN
   RAISE EXCEPTION 'Invalid cash query' USING ERRCODE='22023';
 END IF;
 IF p_receipt THEN
   SELECT receipt_snapshot INTO v_result FROM public.billing_cash_document
     WHERE tenant_id=p_tenant AND id=p_document AND status='confirmed';
   IF NOT FOUND THEN RAISE EXCEPTION 'Confirmed cash document not found' USING ERRCODE='P0002';
     END IF;
 ELSE
   SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item ORDER BY created_at DESC,id)
     FROM (SELECT public.billing_cash_document_json(d,CASE WHEN v_platform THEN p_actor ELSE
       NULL END) AS item,
       d.created_at,d.id FROM public.billing_cash_document d WHERE tenant_id=p_tenant
       AND (v_platform OR status='confirmed') ORDER BY created_at DESC,id LIMIT p_limit OFFSET
         p_offset) rows),'[]'::JSONB),
     'total',(SELECT count(*) FROM public.billing_cash_document WHERE tenant_id=p_tenant
       AND (v_platform OR status='confirmed')),'limit',p_limit,'offset',p_offset) INTO v_result;
 END IF;
 RETURN v_result;
END $$
"""


def upgrade() -> None:
    kernel = _source("0097_add_billing_financial_kernel.py")
    kernel._grant_missing_reference_privileges()
    _execute("""
    CREATE SEQUENCE public.billing_cash_document_number_seq AS BIGINT;
    CREATE TABLE public.billing_cash_document (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL REFERENCES
        public.tenant(id),
      document_number TEXT NOT NULL UNIQUE,amount NUMERIC(14,2) NOT NULL CHECK(amount>0),
      currency TEXT NOT NULL DEFAULT 'TJS' CHECK(currency='TJS'),paid_at TIMESTAMPTZ NOT NULL,
      purpose TEXT NOT NULL CHECK(length(purpose) BETWEEN 3 AND 500),target_invoice_id UUID,
      status TEXT NOT NULL DEFAULT 'pending_approval' CHECK(status IN ('pending_approval',
        'confirmed','rejected')),
      row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version>0),created_at TIMESTAMPTZ NOT
        NULL DEFAULT statement_timestamp(),
      created_by UUID NOT NULL REFERENCES public.app_user(id),created_session_id UUID NOT NULL,
        created_mfa_at TIMESTAMPTZ NOT NULL,
      operation_id UUID NOT NULL UNIQUE,decided_by UUID REFERENCES public.app_user(id),
        decided_session_id UUID,
      decided_mfa_at TIMESTAMPTZ,decided_at TIMESTAMPTZ,decision_operation_id UUID UNIQUE,
        rejection_reason TEXT,
      allocated_amount NUMERIC(14,2) NOT NULL DEFAULT 0,credit_amount NUMERIC(14,2) NOT NULL
        DEFAULT 0,
      receipt_snapshot JSONB,UNIQUE(tenant_id,id),
      FOREIGN KEY(tenant_id,target_invoice_id) REFERENCES public.billing_invoice(tenant_id,id),
      FOREIGN KEY(tenant_id,operation_id) REFERENCES
        public.billing_financial_operation(tenant_id,operation_id) DEFERRABLE INITIALLY DEFERRED,
      FOREIGN KEY(tenant_id,decision_operation_id) REFERENCES
        public.billing_financial_operation(tenant_id,operation_id) DEFERRABLE INITIALLY DEFERRED,
      CHECK((status='pending_approval' AND row_version=1 AND decided_by IS NULL AND
        decided_session_id IS NULL
        AND decided_mfa_at IS NULL AND decided_at IS NULL AND decision_operation_id IS NULL
        AND rejection_reason IS NULL AND receipt_snapshot IS NULL AND allocated_amount=0 AND
          credit_amount=0)
       OR (status IN ('confirmed','rejected') AND row_version=2 AND decided_by IS NOT NULL AND
         decided_by<>created_by
        AND decided_session_id IS NOT NULL AND decided_mfa_at IS NOT NULL AND decided_at IS NOT
          NULL AND decision_operation_id IS NOT NULL
        AND ((status='confirmed' AND rejection_reason IS NULL AND receipt_snapshot IS NOT NULL
          AND allocated_amount>=0 AND credit_amount>=0 AND allocated_amount+credit_amount=amount)
         OR (status='rejected' AND rejection_reason IS NOT NULL
           AND length(rejection_reason) BETWEEN 3 AND 500 AND
           receipt_snapshot IS NULL
          AND allocated_amount=0 AND credit_amount=0))))
    );
    CREATE INDEX ix_cash_document_tenant_created ON public.billing_cash_document(tenant_id,
      created_at DESC,id);
    ALTER TABLE public.billing_cash_document OWNER TO aurum_schema_owner;
    ALTER SEQUENCE public.billing_cash_document_number_seq OWNER TO aurum_schema_owner;
    REVOKE ALL ON SEQUENCE public.billing_cash_document_number_seq FROM PUBLIC,aurum_app,
      aurum_support,aurum_mailer,aurum_edge_cash_executor,aurum_edge_cash_owner;
    ALTER TABLE public.billing_payment
      ALTER COLUMN review_id DROP NOT NULL,ALTER COLUMN recipient_account_key DROP NOT NULL,
        ALTER COLUMN external_reference DROP NOT NULL,
      ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'bank_transfer',ADD COLUMN
        cash_document_id UUID UNIQUE,
      ADD CONSTRAINT fk_billing_payment_cash_document FOREIGN KEY(tenant_id,cash_document_id)
        REFERENCES public.billing_cash_document(tenant_id,id),
      ADD CONSTRAINT ck_billing_payment_source CHECK(
        (payment_method='bank_transfer' AND review_id IS NOT NULL AND cash_document_id IS NULL
          AND recipient_account_key IS NOT NULL AND external_reference IS NOT NULL)
        OR (payment_method='cash' AND review_id IS NULL AND cash_document_id IS NOT NULL AND
          recipient_account_key IS NULL AND external_reference IS NULL));
    ALTER TABLE public.billing_journal_posting DROP CONSTRAINT ck_billing_posting_account,
      ADD CONSTRAINT ck_billing_posting_account CHECK(account_code IN ('accounts_receivable',
        'subscription_revenue','bank_cleared','unapplied_cash','tenant_credit','cash_on_hand'));
    ALTER TABLE public.billing_financial_operation DROP CONSTRAINT
      ck_billing_financial_operation_type,
      ADD CONSTRAINT ck_billing_financial_operation_type CHECK(operation_type IN (
        'invoice_issued','payment_review_created','payment_approved','payment_review_rejected',
        'payment_adjustment_requested','payment_adjustment_approved',
          'payment_adjustment_rejected','payment_submission_review_created',
        'cash_payment_create','cash_payment_approve','cash_payment_reject'));
    """)
    kernel._create_rls("billing_cash_document", tenant_read=False)
    kernel._revoke_table("billing_cash_document", tenant_read=False)
    _execute(r"""
    CREATE FUNCTION public.trg_guard_billing_cash_document() RETURNS TRIGGER
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Cash documents are immutable' USING
        ERRCODE='55000'; END IF;
      IF OLD.status<>'pending_approval' OR NEW.status='pending_approval'
        OR (to_jsonb(NEW)-ARRAY['status','row_version','decided_by','decided_session_id',
          'decided_mfa_at',
          'decided_at','decision_operation_id','rejection_reason','receipt_snapshot',
            'allocated_amount','credit_amount'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','row_version','decided_by',
          'decided_session_id','decided_mfa_at',
          'decided_at','decision_operation_id','rejection_reason','receipt_snapshot',
            'allocated_amount','credit_amount']) THEN
        RAISE EXCEPTION 'Cash documents are immutable' USING ERRCODE='55000';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER trg_guard_billing_cash_document BEFORE UPDATE OR DELETE ON
      public.billing_cash_document
      FOR EACH ROW EXECUTE FUNCTION public.trg_guard_billing_cash_document();
    CREATE TRIGGER trg_audit_billing_cash_document AFTER INSERT OR UPDATE ON
      public.billing_cash_document
      FOR EACH ROW EXECUTE FUNCTION public.trg_audit_billing_financial_row();
    CREATE FUNCTION public.billing_cash_document_json(d public.billing_cash_document,p_actor
      UUID) RETURNS JSONB
    LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      SELECT jsonb_build_object('id',d.id,'tenant_id',d.tenant_id,'document_number',
        d.document_number,
        'status',d.status,'row_version',d.row_version,'amount',d.amount::TEXT,'currency',d.currency,
        'paid_at',d.paid_at,'purpose',d.purpose,'target_invoice_id',d.target_invoice_id,
          'created_by',d.created_by,
        'created_at',d.created_at,'confirmed_at',CASE WHEN d.status='confirmed' THEN
          d.decided_at ELSE NULL END,
        'allocated_amount',d.allocated_amount::TEXT,'credit_amount',d.credit_amount::TEXT,
        'can_approve',COALESCE(p_actor<>d.created_by AND d.status='pending_approval',false),
          'can_adjust',false)
    $$;
    """)
    _execute(COMMAND_SQL)
    _execute(READ_SQL)
    for signature in (
        "public.trg_guard_billing_cash_document()",
        "public.billing_cash_document_json(public.billing_cash_document,UUID)",
    ):
        kernel._secure_function(signature)
    kernel._secure_function(COMMAND_SIGNATURE, grant_support=True)
    kernel._secure_function(READ_SIGNATURE, grant_support=True)
    _execute(f"GRANT EXECUTE ON FUNCTION {READ_SIGNATURE} TO aurum_app")
    # Reject all legacy adjustments for cash at their SQL entry point, before any ledger write.
    _execute(r"""
    CREATE FUNCTION public.trg_guard_cash_adjustment() RETURNS TRIGGER
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF EXISTS(SELECT 1 FROM public.billing_payment WHERE tenant_id=NEW.tenant_id
        AND id=NEW.payment_id AND payment_method='cash') THEN
        RAISE EXCEPTION 'Cash payments require a separate cash return procedure' USING
          ERRCODE='22023';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER trg_guard_cash_adjustment BEFORE INSERT ON
      public.billing_payment_adjustment_request
      FOR EACH ROW EXECUTE FUNCTION public.trg_guard_cash_adjustment();
    """)
    kernel._secure_function("public.trg_guard_cash_adjustment()")
    # Keep both financial projections truthful about the source and available commands.
    for filename, attribute in (
        ("0099_add_billing_adjustments.py", "READ_FINANCIAL_ACCOUNT_SQL"),
        ("0100_add_tenant_billing_financial_account.py", "READ_TENANT_FINANCIAL_ACCOUNT_SQL"),
    ):
        sql = getattr(_source(filename), attribute)
        sql = sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        sql = sql.replace(
            "'amount', payment.amount::TEXT,",
            "'payment_method', payment.payment_method, "
            "'can_adjust', payment.payment_method='bank_transfer', 'amount', payment.amount::TEXT,",
        )
        _execute(sql)
    kernel._restore_reference_privileges()


def downgrade() -> None:
    # Cash records are financial history: never silently destroy them on rollback.
    _execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.billing_cash_document) THEN
      RAISE EXCEPTION 'Cannot downgrade with cash settlement history' USING ERRCODE='55000';
      END IF; END $$""")
    for filename, attribute in (
        ("0099_add_billing_adjustments.py", "READ_FINANCIAL_ACCOUNT_SQL"),
        ("0100_add_tenant_billing_financial_account.py", "READ_TENANT_FINANCIAL_ACCOUNT_SQL"),
    ):
        _execute(
            getattr(_source(filename), attribute).replace(
                "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
            )
        )
    _execute("DROP TRIGGER trg_guard_cash_adjustment ON public.billing_payment_adjustment_request")
    _execute("DROP FUNCTION public.trg_guard_cash_adjustment()")
    _execute(f"DROP FUNCTION {READ_SIGNATURE}")
    _execute(f"DROP FUNCTION {COMMAND_SIGNATURE}")
    _execute("DROP FUNCTION public.billing_cash_document_json(public.billing_cash_document,UUID)")
    _execute("""
    ALTER TABLE public.billing_payment DROP CONSTRAINT ck_billing_payment_source,
      DROP CONSTRAINT fk_billing_payment_cash_document,DROP COLUMN cash_document_id,DROP COLUMN
        payment_method,
      ALTER COLUMN review_id SET NOT NULL,ALTER COLUMN recipient_account_key SET NOT NULL,ALTER
        COLUMN external_reference SET NOT NULL;
    DROP TABLE public.billing_cash_document;
    DROP FUNCTION public.trg_guard_billing_cash_document();
    DROP SEQUENCE public.billing_cash_document_number_seq;
    ALTER TABLE public.billing_journal_posting DROP CONSTRAINT ck_billing_posting_account,
      ADD CONSTRAINT ck_billing_posting_account CHECK(account_code IN ('accounts_receivable',
        'subscription_revenue','bank_cleared','unapplied_cash','tenant_credit'));
    ALTER TABLE public.billing_financial_operation DROP CONSTRAINT
      ck_billing_financial_operation_type,
      ADD CONSTRAINT ck_billing_financial_operation_type CHECK(operation_type IN (
        'invoice_issued','payment_review_created','payment_approved','payment_review_rejected',
        'payment_adjustment_requested','payment_adjustment_approved',
          'payment_adjustment_rejected','payment_submission_review_created'));
    """)
