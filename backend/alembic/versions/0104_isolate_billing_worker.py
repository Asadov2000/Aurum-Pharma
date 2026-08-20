"""isolate subscription transitions behind a dedicated billing worker

Revision ID: 0104
Revises: 0103
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0104"
down_revision: str | None = "0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TRIAL_ENDINGS = "public.process_billing_trial_endings(INTEGER)"
GRACE_ENDINGS = "public.process_billing_grace_endings(INTEGER)"
RUNTIME_ROLES = (
    "PUBLIC",
    "aurum_app",
    "aurum_support",
    "aurum_mailer",
    "aurum_billing_worker",
    "aurum_edge_cash_executor",
    "aurum_edge_cash_owner",
)


TRIAL_ENDINGS_SQL = """
CREATE FUNCTION public.process_billing_trial_endings(
  p_limit INTEGER
) RETURNS INTEGER AS $$
DECLARE
  v_moved INTEGER;
  v_candidate RECORD;
BEGIN
  IF SESSION_USER <> 'aurum_billing_worker' THEN
    RAISE EXCEPTION 'Billing worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'Billing transition batch size must be between 1 and 100'
      USING ERRCODE = '22023';
  END IF;

  v_moved := 0;
  FOR v_candidate IN
    SELECT subscription.id, subscription.tenant_id
    FROM public.tenant_subscription AS subscription
    WHERE subscription.status = 'trial'
      AND subscription.period_end < pg_catalog.statement_timestamp()
    ORDER BY subscription.period_end, subscription.id
    LIMIT p_limit
  LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(v_candidate.tenant_id::TEXT, 9603)
    );
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(v_candidate.tenant_id::TEXT, 9701)
    );
    UPDATE public.tenant_subscription AS subscription
    SET status = 'grace_period',
        updated_at = pg_catalog.statement_timestamp()
    WHERE subscription.id = v_candidate.id
      AND subscription.tenant_id = v_candidate.tenant_id
      AND subscription.status = 'trial'
      AND subscription.period_end < pg_catalog.statement_timestamp();
    IF FOUND THEN
      v_moved := v_moved + 1;
    END IF;
  END LOOP;

  RETURN v_moved;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


GRACE_ENDINGS_SQL = """
CREATE FUNCTION public.process_billing_grace_endings(
  p_limit INTEGER
) RETURNS INTEGER AS $$
DECLARE
  v_moved INTEGER;
  v_candidate RECORD;
BEGIN
  IF SESSION_USER <> 'aurum_billing_worker' THEN
    RAISE EXCEPTION 'Billing worker identity is required' USING ERRCODE = '42501';
  END IF;
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'Billing transition batch size must be between 1 and 100'
      USING ERRCODE = '22023';
  END IF;

  v_moved := 0;
  FOR v_candidate IN
    SELECT subscription.id, subscription.tenant_id
    FROM public.tenant_subscription AS subscription
    WHERE subscription.status = 'grace_period'
      AND subscription.period_end
          < pg_catalog.statement_timestamp() - INTERVAL '7 days'
    ORDER BY subscription.period_end, subscription.id
    LIMIT p_limit
  LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(v_candidate.tenant_id::TEXT, 9603)
    );
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(v_candidate.tenant_id::TEXT, 9701)
    );
    UPDATE public.tenant_subscription AS subscription
    SET status = 'suspended',
        updated_at = pg_catalog.statement_timestamp()
    WHERE subscription.id = v_candidate.id
      AND subscription.tenant_id = v_candidate.tenant_id
      AND subscription.status = 'grace_period'
      AND subscription.period_end
          < pg_catalog.statement_timestamp() - INTERVAL '7 days';
    IF FOUND THEN
      UPDATE public.tenant AS tenant
      SET status = 'readonly',
          updated_at = pg_catalog.statement_timestamp()
      WHERE tenant.id = v_candidate.tenant_id
        AND tenant.status <> 'archived';
      v_moved := v_moved + 1;
    END IF;
  END LOOP;

  RETURN v_moved;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET lock_timeout = '5s'
SET statement_timeout = '30s'
"""


def _secure_worker_function(signature: str) -> None:
    grantees = ", ".join(RUNTIME_ROLES)
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM {grantees}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_billing_worker")


def upgrade() -> None:
    op.execute(TRIAL_ENDINGS_SQL)
    op.execute(GRACE_ENDINGS_SQL)
    _secure_worker_function(TRIAL_ENDINGS)
    _secure_worker_function(GRACE_ENDINGS)
    op.execute("GRANT USAGE ON SCHEMA public TO aurum_billing_worker")


def downgrade() -> None:
    for signature in (GRACE_ENDINGS, TRIAL_ENDINGS):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM aurum_billing_worker")
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("REVOKE USAGE ON SCHEMA public FROM aurum_billing_worker")
