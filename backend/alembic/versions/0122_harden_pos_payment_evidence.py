"""harden POS terminal evidence and payment attempt transitions

Revision ID: 0122
Revises: 0121
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0122"
down_revision: str | Sequence[str] | None = "0121"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_HARDENED_TRANSITION_FUNCTION = """
CREATE OR REPLACE FUNCTION public.trg_guard_pos_payment_attempt_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
  IF ROW(
    NEW.id, NEW.tenant_id, NEW.sale_id, NEW.cashier_user_id,
    NEW.operation_id, NEW.operation_hash, NEW.payment_method,
    NEW.amount, NEW.currency, NEW.evidence_required,
    NEW.created_at, NEW.created_by
  ) IS DISTINCT FROM ROW(
    OLD.id, OLD.tenant_id, OLD.sale_id, OLD.cashier_user_id,
    OLD.operation_id, OLD.operation_hash, OLD.payment_method,
    OLD.amount, OLD.currency, OLD.evidence_required,
    OLD.created_at, OLD.created_by
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
     AND NEW.status NOT IN ('confirmed','consumed') THEN
    RAISE EXCEPTION 'Invalid payment attempt transition';
  ELSIF OLD.status IN ('consumed','voided') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'Final payment attempt is immutable';
  END IF;

  IF NEW.reconciliation_started_at IS DISTINCT FROM OLD.reconciliation_started_at
     AND NOT (OLD.status = 'pending' AND NEW.status = 'requires_reconciliation') THEN
    RAISE EXCEPTION 'Reconciliation timestamp is immutable';
  END IF;
  IF ROW(NEW.terminal_id, NEW.external_reference, NEW.resolved_by_user_id)
     IS DISTINCT FROM
     ROW(OLD.terminal_id, OLD.external_reference, OLD.resolved_by_user_id)
     AND NOT (
       OLD.status = 'requires_reconciliation'
       AND NEW.status IN ('confirmed','voided')
     ) THEN
    RAISE EXCEPTION 'Terminal evidence is immutable';
  END IF;
  IF NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
     AND NOT (OLD.status = 'requires_reconciliation' AND NEW.status = 'confirmed') THEN
    RAISE EXCEPTION 'Payment confirmation timestamp is immutable';
  END IF;
  IF NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
     AND NOT (OLD.status = 'confirmed' AND NEW.status = 'consumed') THEN
    RAISE EXCEPTION 'Payment consumption timestamp is immutable';
  END IF;
  IF NEW.voided_at IS DISTINCT FROM OLD.voided_at
     AND NOT (
       OLD.status IN ('pending','requires_reconciliation')
       AND NEW.status = 'voided'
     ) THEN
    RAISE EXCEPTION 'Payment void timestamp is immutable';
  END IF;
  IF ROW(NEW.void_reason, NEW.void_note) IS DISTINCT FROM
     ROW(OLD.void_reason, OLD.void_note)
     AND NOT (
       OLD.status IN ('pending','requires_reconciliation')
       AND NEW.status = 'voided'
     ) THEN
    RAISE EXCEPTION 'Void details require a voided payment attempt';
  END IF;
  RETURN NEW;
END;
$function$
"""


_LEGACY_TRANSITION_FUNCTION = """
CREATE OR REPLACE FUNCTION public.trg_guard_pos_payment_attempt_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
  IF ROW(
    NEW.id, NEW.tenant_id, NEW.sale_id, NEW.cashier_user_id,
    NEW.operation_id, NEW.operation_hash, NEW.payment_method,
    NEW.amount, NEW.currency, NEW.created_at, NEW.created_by
  ) IS DISTINCT FROM ROW(
    OLD.id, OLD.tenant_id, OLD.sale_id, OLD.cashier_user_id,
    OLD.operation_id, OLD.operation_hash, OLD.payment_method,
    OLD.amount, OLD.currency, OLD.created_at, OLD.created_by
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


_INSERT_GUARD_FUNCTION = """
CREATE FUNCTION public.trg_require_pos_payment_attempt_evidence()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
  IF NOT NEW.evidence_required
     OR NEW.status <> 'pending'
     OR NEW.reconciliation_started_at IS NOT NULL
     OR NEW.terminal_id IS NOT NULL
     OR NEW.external_reference IS NOT NULL
     OR NEW.resolved_by_user_id IS NOT NULL
     OR NEW.confirmed_at IS NOT NULL
     OR NEW.consumed_at IS NOT NULL
     OR NEW.voided_at IS NOT NULL
     OR NEW.void_reason IS NOT NULL
     OR NEW.void_note IS NOT NULL THEN
    RAISE EXCEPTION 'New payment attempts must start pending without terminal evidence';
  END IF;
  RETURN NEW;
END;
$function$
"""


def _add_hardened_state_constraint() -> None:
    op.execute("""
        ALTER TABLE public.pos_payment_attempt
          ADD CONSTRAINT ck_pos_payment_attempt_state_timestamps
          CHECK (
            (
              NOT evidence_required
              AND (
                (status IN ('pending','requires_reconciliation')
                  AND confirmed_at IS NULL AND consumed_at IS NULL
                  AND voided_at IS NULL AND void_reason IS NULL AND void_note IS NULL)
                OR (status = 'confirmed' AND confirmed_at IS NOT NULL
                  AND consumed_at IS NULL AND voided_at IS NULL
                  AND void_reason IS NULL AND void_note IS NULL)
                OR (status = 'consumed' AND confirmed_at IS NOT NULL
                  AND consumed_at IS NOT NULL AND voided_at IS NULL
                  AND void_reason IS NULL AND void_note IS NULL)
                OR (status = 'voided' AND consumed_at IS NULL
                  AND voided_at IS NOT NULL AND void_reason IS NOT NULL)
              )
            )
            OR (
              evidence_required
              AND (
                (status = 'pending' AND reconciliation_started_at IS NULL
                  AND terminal_id IS NULL AND external_reference IS NULL
                  AND resolved_by_user_id IS NULL AND confirmed_at IS NULL
                  AND consumed_at IS NULL AND voided_at IS NULL
                  AND void_reason IS NULL AND void_note IS NULL)
                OR (status = 'requires_reconciliation'
                  AND reconciliation_started_at IS NOT NULL
                  AND terminal_id IS NULL AND external_reference IS NULL
                  AND resolved_by_user_id IS NULL AND confirmed_at IS NULL
                  AND consumed_at IS NULL AND voided_at IS NULL
                  AND void_reason IS NULL AND void_note IS NULL)
                OR (status = 'confirmed' AND reconciliation_started_at IS NOT NULL
                  AND terminal_id IS NOT NULL AND external_reference IS NOT NULL
                  AND resolved_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL
                  AND consumed_at IS NULL AND voided_at IS NULL
                  AND void_reason IS NULL AND void_note IS NULL)
                OR (status = 'consumed' AND reconciliation_started_at IS NOT NULL
                  AND terminal_id IS NOT NULL AND external_reference IS NOT NULL
                  AND resolved_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL
                  AND consumed_at IS NOT NULL AND voided_at IS NULL
                  AND void_reason IS NULL AND void_note IS NULL)
                OR (status = 'voided' AND confirmed_at IS NULL
                  AND consumed_at IS NULL AND voided_at IS NOT NULL
                  AND void_reason IS NOT NULL
                  AND (
                    (reconciliation_started_at IS NULL AND terminal_id IS NULL
                      AND external_reference IS NULL AND resolved_by_user_id IS NULL)
                    OR (reconciliation_started_at IS NOT NULL AND terminal_id IS NOT NULL
                      AND external_reference IS NOT NULL AND resolved_by_user_id IS NOT NULL)
                  ))
              )
            )
          )
        """)


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE
          v_user_had_references BOOLEAN := pg_catalog.has_table_privilege(
            'aurum_schema_owner',
            'public.app_user',
            'REFERENCES'
          );
        BEGIN
          IF NOT v_user_had_references THEN
            GRANT REFERENCES ON TABLE public.app_user TO aurum_schema_owner;
          END IF;

          ALTER TABLE public.pos_payment_attempt
            ADD COLUMN evidence_required BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN reconciliation_started_at TIMESTAMPTZ,
            ADD COLUMN terminal_id TEXT,
            ADD COLUMN resolved_by_user_id UUID
              REFERENCES public.app_user(id) ON DELETE RESTRICT;

          IF NOT v_user_had_references THEN
            REVOKE REFERENCES ON TABLE public.app_user FROM aurum_schema_owner;
          END IF;
        END
        $$
        """)
    op.execute(
        "ALTER TABLE public.pos_payment_attempt " "ALTER COLUMN evidence_required SET DEFAULT true"
    )
    op.execute(
        "ALTER TABLE public.pos_payment_attempt "
        "DROP CONSTRAINT ck_pos_payment_attempt_external_reference"
    )
    op.execute("""
        ALTER TABLE public.pos_payment_attempt
          ADD CONSTRAINT ck_pos_payment_attempt_terminal_id
            CHECK (terminal_id IS NULL OR (
              char_length(terminal_id) BETWEEN 1 AND 64
              AND terminal_id !~ '[[:cntrl:]]'
            )),
          ADD CONSTRAINT ck_pos_payment_attempt_external_reference
            CHECK (external_reference IS NULL OR (
              char_length(external_reference) BETWEEN 1 AND 128
              AND external_reference !~ '[[:cntrl:]]'
            ))
        """)
    op.execute(
        "ALTER TABLE public.pos_payment_attempt "
        "DROP CONSTRAINT ck_pos_payment_attempt_state_timestamps"
    )
    _add_hardened_state_constraint()
    op.execute("""
        CREATE UNIQUE INDEX uq_pos_payment_attempt_terminal_reference
        ON public.pos_payment_attempt (tenant_id, terminal_id, external_reference)
        WHERE terminal_id IS NOT NULL AND external_reference IS NOT NULL
        """)
    op.execute(_HARDENED_TRANSITION_FUNCTION)
    op.execute(_INSERT_GUARD_FUNCTION)
    op.execute("""
        REVOKE ALL ON FUNCTION public.trg_require_pos_payment_attempt_evidence()
          FROM PUBLIC, aurum_app, aurum_support
        """)
    op.execute("""
        CREATE TRIGGER trg_pos_payment_attempt_evidence_insert
          BEFORE INSERT ON public.pos_payment_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_require_pos_payment_attempt_evidence()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_pos_payment_attempt_evidence_insert " "ON public.pos_payment_attempt"
    )
    op.execute("DROP FUNCTION public.trg_require_pos_payment_attempt_evidence()")
    op.execute("DROP INDEX public.uq_pos_payment_attempt_terminal_reference")
    op.execute(
        "ALTER TABLE public.pos_payment_attempt "
        "DROP CONSTRAINT ck_pos_payment_attempt_state_timestamps"
    )
    op.execute("""
        ALTER TABLE public.pos_payment_attempt
          ADD CONSTRAINT ck_pos_payment_attempt_state_timestamps
          CHECK (
            (status IN ('pending','requires_reconciliation')
              AND confirmed_at IS NULL AND consumed_at IS NULL AND voided_at IS NULL
              AND void_reason IS NULL AND void_note IS NULL)
            OR (status = 'confirmed' AND confirmed_at IS NOT NULL
              AND consumed_at IS NULL AND voided_at IS NULL
              AND void_reason IS NULL AND void_note IS NULL)
            OR (status = 'consumed' AND confirmed_at IS NOT NULL
              AND consumed_at IS NOT NULL AND voided_at IS NULL
              AND void_reason IS NULL AND void_note IS NULL)
            OR (status = 'voided' AND consumed_at IS NULL
              AND voided_at IS NOT NULL AND void_reason IS NOT NULL)
          )
        """)
    op.execute(_LEGACY_TRANSITION_FUNCTION)
    op.execute(
        "ALTER TABLE public.pos_payment_attempt "
        "DROP CONSTRAINT ck_pos_payment_attempt_external_reference"
    )
    op.execute(
        "ALTER TABLE public.pos_payment_attempt "
        "DROP CONSTRAINT ck_pos_payment_attempt_terminal_id"
    )
    op.execute("""
        ALTER TABLE public.pos_payment_attempt
          ADD CONSTRAINT ck_pos_payment_attempt_external_reference
          CHECK (external_reference IS NULL OR char_length(external_reference) <= 128)
        """)
    op.execute("""
        ALTER TABLE public.pos_payment_attempt
          DROP COLUMN resolved_by_user_id,
          DROP COLUMN terminal_id,
          DROP COLUMN reconciliation_started_at,
          DROP COLUMN evidence_required
        """)
