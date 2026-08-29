"""add authoritative reconciliation state to external POS attempts

Revision ID: 0118
Revises: 0117
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0118"
down_revision: str | Sequence[str] | None = "0117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PAYMENT_TRANSITION_FUNCTION = """
CREATE OR REPLACE FUNCTION public.trg_guard_pos_payment_attempt_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
  IF ROW(
    NEW.id,
    NEW.tenant_id,
    NEW.sale_id,
    NEW.cashier_user_id,
    NEW.operation_id,
    NEW.operation_hash,
    NEW.payment_method,
    NEW.amount,
    NEW.currency,
    NEW.created_at,
    NEW.created_by
  ) IS DISTINCT FROM ROW(
    OLD.id,
    OLD.tenant_id,
    OLD.sale_id,
    OLD.cashier_user_id,
    OLD.operation_id,
    OLD.operation_hash,
    OLD.payment_method,
    OLD.amount,
    OLD.currency,
    OLD.created_at,
    OLD.created_by
  ) THEN
    RAISE EXCEPTION 'Payment attempt identity is immutable';
  END IF;

  IF OLD.status = 'pending'
     AND NEW.status NOT IN ('pending','requires_reconciliation','voided') THEN
    RAISE EXCEPTION 'Invalid payment attempt transition';
  ELSIF OLD.status = 'requires_reconciliation'
     AND NEW.status NOT IN ('requires_reconciliation','confirmed','voided') THEN
    RAISE EXCEPTION 'Invalid payment attempt transition';
  ELSIF OLD.status = 'confirmed'
     AND NEW.status NOT IN ('confirmed','consumed','voided') THEN
    RAISE EXCEPTION 'Invalid payment attempt transition';
  ELSIF OLD.status IN ('consumed','voided') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'Final payment attempt is immutable';
  END IF;

  IF NEW.external_reference IS DISTINCT FROM OLD.external_reference
     AND NOT (
       OLD.status IN ('pending','requires_reconciliation')
       AND NEW.status = 'confirmed'
     ) THEN
    RAISE EXCEPTION 'External payment reference is immutable';
  END IF;
  IF NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
     AND NOT (
       OLD.status IN ('pending','requires_reconciliation')
       AND NEW.status = 'confirmed'
     ) THEN
    RAISE EXCEPTION 'Payment confirmation timestamp is immutable';
  END IF;
  IF NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
     AND NOT (OLD.status = 'confirmed' AND NEW.status = 'consumed') THEN
    RAISE EXCEPTION 'Payment consumption timestamp is immutable';
  END IF;
  IF NEW.voided_at IS DISTINCT FROM OLD.voided_at
     AND NOT (
       OLD.status IN ('pending','requires_reconciliation','confirmed')
       AND NEW.status = 'voided'
     ) THEN
    RAISE EXCEPTION 'Payment void timestamp is immutable';
  END IF;
  IF ROW(NEW.void_reason, NEW.void_note) IS DISTINCT FROM
     ROW(OLD.void_reason, OLD.void_note)
     AND NOT (
       OLD.status IN ('pending','requires_reconciliation','confirmed')
       AND NEW.status = 'voided'
     ) THEN
    RAISE EXCEPTION 'Void details require a voided payment attempt';
  END IF;
  RETURN NEW;
END;
$function$
"""


_REFUND_TRANSITION_FUNCTION = """
CREATE OR REPLACE FUNCTION public.trg_guard_pos_refund_attempt_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
  IF ROW(
    NEW.id, NEW.tenant_id, NEW.parent_sale_id, NEW.register_id,
    NEW.requested_by_user_id, NEW.operation_id, NEW.operation_hash,
    NEW.items_json, NEW.external_allocations_json, NEW.total_amount,
    NEW.external_amount, NEW.currency, NEW.created_at, NEW.created_by
  ) IS DISTINCT FROM ROW(
    OLD.id, OLD.tenant_id, OLD.parent_sale_id, OLD.register_id,
    OLD.requested_by_user_id, OLD.operation_id, OLD.operation_hash,
    OLD.items_json, OLD.external_allocations_json, OLD.total_amount,
    OLD.external_amount, OLD.currency, OLD.created_at, OLD.created_by
  ) THEN
    RAISE EXCEPTION 'Refund attempt identity is immutable';
  END IF;
  IF OLD.status = 'pending'
     AND NEW.status NOT IN ('pending','requires_reconciliation','voided') THEN
    RAISE EXCEPTION 'Invalid refund attempt transition';
  ELSIF OLD.status = 'requires_reconciliation'
     AND NEW.status NOT IN ('requires_reconciliation','confirmed','voided') THEN
    RAISE EXCEPTION 'Invalid refund attempt transition';
  ELSIF OLD.status = 'confirmed'
     AND NEW.status NOT IN ('confirmed','consumed') THEN
    RAISE EXCEPTION 'Invalid refund attempt transition';
  ELSIF OLD.status IN ('consumed','voided') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'Final refund attempt is immutable';
  END IF;
  IF ROW(NEW.confirmed_by_user_id, NEW.confirmed_at) IS DISTINCT FROM
     ROW(OLD.confirmed_by_user_id, OLD.confirmed_at)
     AND NOT (
       OLD.status IN ('pending','requires_reconciliation')
       AND NEW.status = 'confirmed'
     ) THEN
    RAISE EXCEPTION 'Refund confirmation is immutable';
  END IF;
  IF NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
     AND NOT (OLD.status = 'confirmed' AND NEW.status = 'consumed') THEN
    RAISE EXCEPTION 'Refund consumption timestamp is immutable';
  END IF;
  IF ROW(NEW.void_reason, NEW.void_note, NEW.voided_at) IS DISTINCT FROM
     ROW(OLD.void_reason, OLD.void_note, OLD.voided_at)
     AND NOT (
       OLD.status IN ('pending','requires_reconciliation')
       AND NEW.status = 'voided'
     ) THEN
    RAISE EXCEPTION 'Refund void details are immutable';
  END IF;
  RETURN NEW;
END;
$function$
"""


def _replace_constraints(*, include_reconciliation: bool) -> None:
    statuses = (
        "'pending','requires_reconciliation','confirmed','consumed','voided'"
        if include_reconciliation
        else "'pending','confirmed','consumed','voided'"
    )
    pending_states = (
        "status IN ('pending','requires_reconciliation')"
        if include_reconciliation
        else "status = 'pending'"
    )
    op.execute("ALTER TABLE public.pos_payment_attempt DROP CONSTRAINT ck_pos_payment_attempt_status")
    op.execute(
        "ALTER TABLE public.pos_payment_attempt "
        "DROP CONSTRAINT ck_pos_payment_attempt_state_timestamps"
    )
    op.execute(f"""
        ALTER TABLE public.pos_payment_attempt
          ADD CONSTRAINT ck_pos_payment_attempt_status
            CHECK (status IN ({statuses})),
          ADD CONSTRAINT ck_pos_payment_attempt_state_timestamps
            CHECK (
              ({pending_states}
                AND confirmed_at IS NULL AND consumed_at IS NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'confirmed'
                AND confirmed_at IS NOT NULL AND consumed_at IS NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'consumed'
                AND confirmed_at IS NOT NULL AND consumed_at IS NOT NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'voided'
                AND consumed_at IS NULL AND voided_at IS NOT NULL AND void_reason IS NOT NULL)
            )
        """)

    op.execute("DROP INDEX public.uq_pos_refund_attempt_active_sale")
    op.execute("ALTER TABLE public.pos_refund_attempt DROP CONSTRAINT ck_pos_refund_attempt_status")
    op.execute("ALTER TABLE public.pos_refund_attempt DROP CONSTRAINT ck_pos_refund_attempt_state")
    op.execute(f"""
        ALTER TABLE public.pos_refund_attempt
          ADD CONSTRAINT ck_pos_refund_attempt_status
            CHECK (status IN ({statuses})),
          ADD CONSTRAINT ck_pos_refund_attempt_state
            CHECK (
              ({pending_states} AND confirmed_by_user_id IS NULL
                AND confirmed_at IS NULL AND consumed_at IS NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'confirmed' AND confirmed_by_user_id IS NOT NULL
                AND confirmed_at IS NOT NULL AND consumed_at IS NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'consumed' AND confirmed_by_user_id IS NOT NULL
                AND confirmed_at IS NOT NULL AND consumed_at IS NOT NULL AND voided_at IS NULL
                AND void_reason IS NULL AND void_note IS NULL)
              OR (status = 'voided' AND confirmed_by_user_id IS NULL
                AND confirmed_at IS NULL AND consumed_at IS NULL
                AND voided_at IS NOT NULL AND void_reason IS NOT NULL)
            )
        """)
    active_statuses = (
        "'pending','requires_reconciliation','confirmed'"
        if include_reconciliation
        else "'pending','confirmed'"
    )
    op.execute(f"""
        CREATE UNIQUE INDEX uq_pos_refund_attempt_active_sale
        ON public.pos_refund_attempt (tenant_id, parent_sale_id)
        WHERE status IN ({active_statuses})
        """)


def upgrade() -> None:
    _replace_constraints(include_reconciliation=True)
    op.execute(_PAYMENT_TRANSITION_FUNCTION)
    op.execute(_REFUND_TRANSITION_FUNCTION)


def downgrade() -> None:
    op.execute(
        "UPDATE public.pos_payment_attempt SET status = 'pending' "
        "WHERE status = 'requires_reconciliation'"
    )
    op.execute(
        "UPDATE public.pos_refund_attempt SET status = 'pending' "
        "WHERE status = 'requires_reconciliation'"
    )
    _replace_constraints(include_reconciliation=False)
    # The original functions are recreated by the historical definitions below.
    op.execute(_PAYMENT_TRANSITION_FUNCTION.replace(
        "'pending','requires_reconciliation','voided'",
        "'pending','confirmed','voided'",
    ).replace(
        "ELSIF OLD.status = 'requires_reconciliation'\n"
        "     AND NEW.status NOT IN ('requires_reconciliation','confirmed','voided') THEN\n"
        "    RAISE EXCEPTION 'Invalid payment attempt transition';\n",
        "",
    ).replace("('pending','requires_reconciliation')", "('pending')").replace(
        "('pending','requires_reconciliation','confirmed')",
        "('pending','confirmed')",
    ))
    op.execute(_REFUND_TRANSITION_FUNCTION.replace(
        "'pending','requires_reconciliation','voided'",
        "'pending','confirmed','voided'",
    ).replace(
        "ELSIF OLD.status = 'requires_reconciliation'\n"
        "     AND NEW.status NOT IN ('requires_reconciliation','confirmed','voided') THEN\n"
        "    RAISE EXCEPTION 'Invalid refund attempt transition';\n",
        "",
    ).replace("('pending','requires_reconciliation')", "('pending')"))
