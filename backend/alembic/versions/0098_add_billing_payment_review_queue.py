"""add protected billing payment review queue

Revision ID: 0098
Revises: 0097
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0098"
down_revision: str | None = "0097"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LIST_PAYMENT_REVIEW_QUEUE_SQL = r"""
CREATE FUNCTION public.list_platform_billing_payment_reviews(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_tenant_id UUID,
  p_limit INTEGER,
  p_offset INTEGER
)
RETURNS JSONB AS $$
DECLARE
  v_result JSONB;
BEGIN
  PERFORM public.assert_and_lock_platform_recent_capability(
    p_actor_user_id, p_actor_session_id, 'platform.billing.payment.approve'
  );
  IF p_tenant_id IS NULL
    OR p_limit < 1 OR p_limit > 100
    OR p_offset < 0
    OR NOT EXISTS (
      SELECT 1 FROM public.tenant AS tenant WHERE tenant.id = p_tenant_id
    )
  THEN
    RAISE EXCEPTION 'Invalid billing payment review queue request'
      USING ERRCODE = '22023';
  END IF;

  SELECT pg_catalog.jsonb_build_object(
    'items', COALESCE((
      SELECT pg_catalog.jsonb_agg(
        queue.item ORDER BY queue.created_at, queue.review_id
      )
      FROM (
        SELECT
          review.created_at,
          review.id AS review_id,
          pg_catalog.jsonb_build_object(
            'review_id', review.id,
            'tenant_id', review.tenant_id,
            'tenant_name', tenant.name,
            'target_invoice_id', review.target_invoice_id,
            'invoice_number', invoice.invoice_number,
            'amount', review.amount::TEXT,
            'currency', review.currency,
            'paid_at', review.paid_at,
            'status', review.status,
            'row_version', review.row_version,
            'created_at', review.created_at,
            'is_own_review', review.reviewed_by = p_actor_user_id
          ) AS item
        FROM public.billing_payment_review AS review
        JOIN public.tenant AS tenant ON tenant.id = review.tenant_id
        JOIN public.billing_invoice AS invoice
          ON invoice.tenant_id = review.tenant_id
         AND invoice.id = review.target_invoice_id
        WHERE review.tenant_id = p_tenant_id
          AND review.status = 'pending_approval'
        ORDER BY review.created_at, review.id
        LIMIT p_limit OFFSET p_offset
      ) AS queue
    ), '[]'::JSONB),
    'total', (
      SELECT pg_catalog.count(*)
      FROM public.billing_payment_review AS review
      WHERE review.tenant_id = p_tenant_id
        AND review.status = 'pending_approval'
    )
  ) INTO v_result;
  RETURN v_result;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SIGNATURE = "public.list_platform_billing_payment_reviews(UUID, UUID, UUID, INTEGER, INTEGER)"


def upgrade() -> None:
    op.execute(LIST_PAYMENT_REVIEW_QUEUE_SQL)
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {SIGNATURE} "
        "FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, "
        "aurum_edge_cash_executor, aurum_edge_cash_owner"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_support")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
