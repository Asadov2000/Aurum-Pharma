"""Retire subscription commands without deleting financial history.

Revision ID: 0157
Revises: 0156
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0157"
down_revision = "0156"
branch_labels = None
depends_on = None

# Runtime commands, including SECURITY DEFINER functions, retain SESSION_USER.
# Only the migration identity may import historical records. No application flag
# or impersonatable GUC can re-enable subscription writes.
FROZEN_TABLES = (
    "subscription_plan",
    "tenant_subscription",
    "billing_plan",
    "billing_price_version",
    "billing_contract_override",
    "billing_subscription_price_application",
    "billing_invoice",
    "billing_invoice_line",
    "trial_activation",
)


def _source(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"subscription_retirement_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load canonical migration: {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replace_function(sql: str) -> None:
    op.execute(sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.trg_retired_subscription_write() RETURNS TRIGGER
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp
            SET lock_timeout='5s' SET statement_timeout='30s' AS $$
        BEGIN
          IF SESSION_USER <> 'aurum_migrator' THEN
            RAISE EXCEPTION 'subscriptions_disabled' USING ERRCODE='42501';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END;
        $$
    """)
    op.execute("ALTER FUNCTION public.trg_retired_subscription_write() OWNER TO aurum_schema_owner")
    op.execute("REVOKE ALL ON FUNCTION public.trg_retired_subscription_write() FROM PUBLIC")
    for table in FROZEN_TABLES:
        op.execute(f"""
            CREATE TRIGGER a00_retired_subscription_write
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_retired_subscription_write()
        """)
    op.execute("""
        CREATE TRIGGER a00_retired_subscription_write BEFORE INSERT
        ON public.billing_payment_submission FOR EACH ROW
        EXECUTE FUNCTION public.trg_retired_subscription_write()
    """)

    # Old queued jobs and old worker binaries are harmless after the migration.
    for name in ("process_billing_trial_endings", "process_billing_grace_endings"):
        op.execute(f"""
            CREATE OR REPLACE FUNCTION public.{name}(p_limit INTEGER) RETURNS INTEGER
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp
            SET lock_timeout='5s' SET statement_timeout='30s' AS $$
            BEGIN
              IF SESSION_USER <> 'aurum_billing_worker' THEN
                RAISE EXCEPTION 'Billing worker identity is required' USING ERRCODE='42501';
              END IF;
              IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
                RAISE EXCEPTION 'Invalid transition batch' USING ERRCODE='22023';
              END IF;
              RETURN 0;
            END;
            $$
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.worker_list_automatic_trial_candidates(
          p_limit INTEGER, p_tenant_ids UUID[] DEFAULT NULL
        ) RETURNS TABLE(tenant_id UUID)
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
        SET lock_timeout='5s' SET statement_timeout='30s' AS $$
        BEGIN
          IF SESSION_USER <> 'aurum_worker' THEN
            RAISE EXCEPTION 'System worker identity is required' USING ERRCODE='42501';
          END IF;
          IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
            RAISE EXCEPTION 'Invalid automatic trial batch' USING ERRCODE='22023';
          END IF;
          RETURN;
        END;
        $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.worker_start_automatic_trial(p_tenant_id UUID)
        RETURNS TABLE(started BOOLEAN, reason TEXT)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp
            SET lock_timeout='5s' SET statement_timeout='30s' AS $$
        BEGIN
          IF SESSION_USER <> 'aurum_worker' THEN
            RAISE EXCEPTION 'System worker identity is required' USING ERRCODE='42501';
          END IF;
          IF p_tenant_id IS NULL THEN
            RAISE EXCEPTION 'Tenant id is required' USING ERRCODE='22023';
          END IF;
          RETURN QUERY SELECT false, 'subscriptions_disabled'::TEXT;
        END;
        $$
    """)

    # Preserve the established double-entry, allocation and idempotency command;
    # remove only its commercial access side effect. Historical replay payloads
    # remain immutable, including their original access_restored value.
    sql: str = _source("0097_add_billing_financial_kernel.py").APPROVE_PAYMENT_SQL
    start = sql.index("  SELECT * INTO v_subscription\n", sql.index("CREATE FUNCTION"))
    end = sql.index("  UPDATE public.billing_payment_review\n", start)
    removed = sql[start:end]
    if "v_access_restored := true;" not in removed or "UPDATE public.tenant\n" not in removed:
        raise RuntimeError("Canonical payment activation block changed")
    historical_read = """
  -- Preserve the historical result contract without changing commercial state.
  SELECT * INTO v_subscription FROM public.tenant_subscription
    WHERE tenant_id = p_tenant_id AND id = v_target_invoice.subscription_id;
  -- Payments never alter tenant access.

"""
    _replace_function(sql[:start] + historical_read + sql[end:])


def downgrade() -> None:
    # Downgrade is a coordinated rollback: restore the old application/scheduler
    # only after this migration. Never use it to restore a single tenant's access.
    op.execute("DROP TRIGGER a00_retired_subscription_write ON public.billing_payment_submission")
    for table in reversed(FROZEN_TABLES):
        op.execute(f"DROP TRIGGER a00_retired_subscription_write ON public.{table}")
    op.execute("DROP FUNCTION public.trg_retired_subscription_write()")
    billing = _source("0104_isolate_billing_worker.py")
    _replace_function(billing.TRIAL_ENDINGS_SQL)
    _replace_function(billing.GRACE_ENDINGS_SQL)
    worker = _source("0137_isolate_system_worker.py")
    _replace_function(worker.TRIAL_CANDIDATES_SQL)
    _replace_function(worker.START_AUTOMATIC_TRIAL_SQL)
    _replace_function(_source("0097_add_billing_financial_kernel.py").APPROVE_PAYMENT_SQL)
