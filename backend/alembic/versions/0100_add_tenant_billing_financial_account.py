"""billing: expose a tenant-scoped financial account projection

Revision ID: 0100
Revises: 0099
Create Date: 2026-08-15

The financial kernel deliberately denies the application role direct access to
its journal tables. This projection is the narrow read boundary for tenant-wide
roles that hold both dedicated billing read permissions.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0100"
down_revision: str | Sequence[str] | None = "0099"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FUNCTION_SIGNATURE = "public.read_tenant_billing_financial_account(UUID, UUID)"
TENANT_BILLING_PERMISSIONS = (
    (
        "billing.overview.view",
        "Просмотр финансовой сводки",
        "Просмотр статуса тарифа, задолженности, аванса и подтвержденных платежей аптеки.",
    ),
    (
        "billing.invoice.view",
        "Просмотр счетов Aurum Pharma",
        "Просмотр выставленных аптеке счетов, сроков оплаты и непогашенного остатка.",
    ),
)


READ_TENANT_FINANCIAL_ACCOUNT_SQL = r"""
CREATE FUNCTION public.read_tenant_billing_financial_account(
  p_actor_user_id UUID,
  p_tenant_id UUID
)
RETURNS JSONB AS $$
DECLARE
  v_result JSONB;
BEGIN
  IF SESSION_USER <> 'aurum_app'
    OR COALESCE(
      pg_catalog.current_setting('app.support_session', true), ''
    ) = 'true'
    OR public.is_support_session()
    OR public.is_tenant_support_session()
    OR p_actor_user_id IS NULL
    OR p_actor_user_id IS DISTINCT FROM public.current_app_user_id()
    OR p_tenant_id IS NULL
    OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR NOT public.tenant_actor_has_permission(p_tenant_id, 'billing.overview.view')
    OR NOT public.tenant_actor_has_permission(p_tenant_id, 'billing.invoice.view')
  THEN
    RAISE EXCEPTION 'Tenant billing account is unavailable'
      USING ERRCODE = '42501';
  END IF;

  SELECT pg_catalog.jsonb_build_object(
    'subscription', (
      SELECT pg_catalog.jsonb_build_object(
        'status', subscription.status,
        'plan_name', COALESCE(application.plan_name, legacy_plan.name),
        'billing_period', COALESCE(application.billing_period, subscription.billing_period),
        'period_start', COALESCE(application.period_start, subscription.period_start),
        'period_end', COALESCE(application.period_end, subscription.period_end),
        'branches_count', COALESCE(application.branches_count, subscription.branches_count),
        'amount', COALESCE(
          application.calculated_amount, subscription.amount
        )::NUMERIC(14,2)::TEXT,
        'currency', COALESCE(application.currency, subscription.currency)
      )
      FROM public.tenant_subscription AS subscription
      JOIN public.subscription_plan AS legacy_plan
        ON legacy_plan.id = subscription.plan_id
      LEFT JOIN LATERAL (
        SELECT
          price_application.plan_name,
          price_application.billing_period,
          price_application.period_start,
          price_application.period_end,
          price_application.branches_count,
          price_application.calculated_amount,
          price_application.currency
        FROM public.billing_subscription_price_application AS price_application
        WHERE price_application.tenant_id = subscription.tenant_id
          AND price_application.subscription_id = subscription.id
        ORDER BY
          price_application.period_start DESC,
          price_application.created_at DESC,
          price_application.id DESC
        LIMIT 1
      ) AS application ON true
      WHERE subscription.tenant_id = p_tenant_id
        AND subscription.status NOT IN ('cancelled', 'archived')
      LIMIT 1
    ),
    'currency', 'TJS',
    'outstanding_amount', COALESCE((
      SELECT sum(GREATEST(receivable.amount, 0))
      FROM (
        SELECT
          invoice.id,
          COALESCE(sum(CASE posting.side
            WHEN 'debit' THEN posting.amount ELSE -posting.amount END), 0) AS amount
        FROM public.billing_invoice AS invoice
        LEFT JOIN public.billing_journal_posting AS posting
          ON posting.tenant_id = invoice.tenant_id
         AND posting.invoice_id = invoice.id
         AND posting.account_code = 'accounts_receivable'
        WHERE invoice.tenant_id = p_tenant_id
          AND invoice.document_state = 'issued'
        GROUP BY invoice.id
      ) AS receivable
    ), 0)::NUMERIC(14,2)::TEXT,
    'credit_balance', COALESCE((
      SELECT sum(CASE posting.side
        WHEN 'credit' THEN posting.amount ELSE -posting.amount END)
      FROM public.billing_journal_posting AS posting
      WHERE posting.tenant_id = p_tenant_id
        AND posting.account_code = 'tenant_credit'
    ), 0)::NUMERIC(14,2)::TEXT,
    'invoices', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'invoice_id', invoice.id,
          'invoice_number', invoice.invoice_number,
          'document_state', invoice.document_state,
          'settlement_state', CASE
            WHEN GREATEST(COALESCE(receivable.amount, invoice.total_amount), 0) <= 0
              THEN 'paid'
            WHEN GREATEST(COALESCE(receivable.amount, invoice.total_amount), 0)
              < invoice.total_amount THEN 'partially_paid'
            ELSE 'unpaid'
          END,
          'collection_state', CASE
            WHEN GREATEST(COALESCE(receivable.amount, invoice.total_amount), 0) <= 0
              THEN 'not_due'
            WHEN invoice.due_at < pg_catalog.statement_timestamp() THEN 'overdue'
            WHEN invoice.due_at > pg_catalog.statement_timestamp() THEN 'not_due'
            ELSE 'due'
          END,
          'period_start', invoice.period_start,
          'period_end', invoice.period_end,
          'due_at', invoice.due_at,
          'total_amount', invoice.total_amount::TEXT,
          'outstanding_amount', GREATEST(
            COALESCE(receivable.amount, invoice.total_amount), 0
          )::NUMERIC(14,2)::TEXT,
          'currency', invoice.currency,
          'issued_at', invoice.issued_at
        ) ORDER BY invoice.due_at DESC, invoice.issued_at DESC, invoice.id
      )
      FROM public.billing_invoice AS invoice
      LEFT JOIN LATERAL (
        SELECT COALESCE(sum(CASE posting.side
          WHEN 'debit' THEN posting.amount ELSE -posting.amount END), 0) AS amount
        FROM public.billing_journal_posting AS posting
        WHERE posting.tenant_id = invoice.tenant_id
          AND posting.invoice_id = invoice.id
          AND posting.account_code = 'accounts_receivable'
      ) AS receivable ON true
      WHERE invoice.tenant_id = p_tenant_id
    ), '[]'::JSONB),
    'payments', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'amount', payment.amount::TEXT,
          'allocated_amount', GREATEST(
            COALESCE(allocated.amount, 0) - COALESCE(reversed.allocated_amount, 0), 0
          )::NUMERIC(14,2)::TEXT,
          'credit_amount', GREATEST(
            COALESCE(credit.amount, 0) - COALESCE(reversed.credit_amount, 0), 0
          )::NUMERIC(14,2)::TEXT,
          'corrected_amount', COALESCE(reversed.corrected_amount, 0)::TEXT,
          'refunded_amount', COALESCE(reversed.refunded_amount, 0)::TEXT,
          'currency', payment.currency,
          'paid_at', payment.paid_at,
          'confirmed_at', payment.confirmed_at,
          'lifecycle_state', CASE
            WHEN payment.amount - COALESCE(reversed.total_amount, 0) <= 0
              THEN 'reversed' ELSE 'confirmed' END
        ) ORDER BY payment.paid_at DESC, payment.confirmed_at DESC, payment.id
      )
      FROM public.billing_payment AS payment
      LEFT JOIN LATERAL (
        SELECT COALESCE(sum(allocation.amount), 0)::NUMERIC(14,2) AS amount
        FROM public.billing_payment_allocation AS allocation
        WHERE allocation.tenant_id = payment.tenant_id
          AND allocation.payment_id = payment.id
      ) AS allocated ON true
      LEFT JOIN public.billing_tenant_credit AS credit
        ON credit.tenant_id = payment.tenant_id
       AND credit.payment_id = payment.id
      LEFT JOIN LATERAL (
        SELECT
          COALESCE(sum(adjustment.amount), 0)::NUMERIC(14,2) AS total_amount,
          COALESCE(sum(adjustment.credit_amount), 0)::NUMERIC(14,2) AS credit_amount,
          COALESCE(sum(adjustment.amount) FILTER (
            WHERE adjustment.adjustment_kind = 'correction'
          ), 0)::NUMERIC(14,2) AS corrected_amount,
          COALESCE(sum(adjustment.amount) FILTER (
            WHERE adjustment.adjustment_kind = 'bank_refund'
          ), 0)::NUMERIC(14,2) AS refunded_amount,
          COALESCE((
            SELECT sum(reversal.amount)
            FROM public.billing_payment_adjustment_allocation AS reversal
            WHERE reversal.tenant_id = payment.tenant_id
              AND reversal.payment_id = payment.id
          ), 0)::NUMERIC(14,2) AS allocated_amount
        FROM public.billing_payment_adjustment AS adjustment
        WHERE adjustment.tenant_id = payment.tenant_id
          AND adjustment.payment_id = payment.id
      ) AS reversed ON true
      WHERE payment.tenant_id = p_tenant_id
    ), '[]'::JSONB)
  ) INTO v_result;

  RETURN v_result;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def upgrade() -> None:
    for code, name, description in TENANT_BILLING_PERMISSIONS:
        op.execute(f"""
            INSERT INTO public.permission (
              code, group_code, name, description, min_level_required,
              is_dangerous, is_active, scope_type, target_role_type, risk_level,
              developer_grantable, administrator_grantable, owner_grantable,
              developer_delegable, administrator_delegable, owner_delegable,
              requires_step_up, requires_confirmation
            ) VALUES (
              '{code}', 'billing', '{name}', '{description}', 3,
              false, true, 'TENANT_ALL', 'tenant', 'normal',
              true, true, true, true, true, true, false, false
            )
            ON CONFLICT (code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.role_permission (role_id, permission_code)
            SELECT role.id, '{code}'
            FROM public.role AS role
            WHERE role.is_system = true
            ON CONFLICT (role_id, permission_code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.role_template_permission (template_id, permission_code)
            SELECT template.id, '{code}'
            FROM public.role_template AS template
            WHERE template.slug = 'owner' AND template.is_active
            ON CONFLICT (template_id, permission_code) DO NOTHING
            """)
        op.execute(f"""
            INSERT INTO public.role_permission (role_id, permission_code)
            SELECT role.id, '{code}'
            FROM public.role AS role
            WHERE role.is_protected = true
              AND role.protected_kind = 'tenant_owner'
              AND role.is_active = true
            ON CONFLICT (role_id, permission_code) DO NOTHING
            """)

    op.execute(READ_TENANT_FINANCIAL_ACCOUNT_SQL)
    op.execute(f"ALTER FUNCTION {FUNCTION_SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION_SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_edge_cash_executor, aurum_edge_cash_owner"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION_SIGNATURE} TO aurum_app")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION_SIGNATURE}")
    codes = ", ".join(f"'{code}'" for code, _, _ in TENANT_BILLING_PERMISSIONS)
    op.execute(f"DELETE FROM public.role_template_permission WHERE permission_code IN ({codes})")
    op.execute(f"DELETE FROM public.role_permission WHERE permission_code IN ({codes})")
    op.execute(f"DELETE FROM public.permission WHERE code IN ({codes})")
