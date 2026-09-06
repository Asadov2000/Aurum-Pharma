"""harden terminal evidence and recoverable refund intent

Revision ID: 0132
Revises: 0131
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0132"
down_revision: str | Sequence[str] | None = "0131"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PAYMENT_GUARD = """
CREATE FUNCTION public.trg_guard_payment_evidence_cross_type()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
  IF NEW.terminal_id IS NULL OR NEW.external_reference IS NULL
     OR ROW(NEW.terminal_id, NEW.external_reference) IS NOT DISTINCT FROM
        ROW(OLD.terminal_id, OLD.external_reference) THEN
    RETURN NEW;
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      NEW.tenant_id::text || chr(31) || NEW.terminal_id || chr(31) || NEW.external_reference,
      0
    )
  );

  IF EXISTS (
    SELECT 1
    FROM public.pos_refund_reference AS reference
    WHERE reference.tenant_id = NEW.tenant_id
      AND reference.terminal_id = NEW.terminal_id
      AND reference.document_number = NEW.external_reference
  ) THEN
    RAISE unique_violation
      USING MESSAGE = 'Terminal evidence was already used by a refund',
            CONSTRAINT = 'uq_pos_terminal_evidence_cross_type';
  END IF;
  RETURN NEW;
END;
$function$
"""


_REFUND_GUARD = """
CREATE FUNCTION public.trg_guard_refund_evidence_cross_type()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      NEW.tenant_id::text || chr(31) || NEW.terminal_id || chr(31) || NEW.document_number,
      0
    )
  );

  IF EXISTS (
    SELECT 1
    FROM public.pos_payment_attempt AS attempt
    WHERE attempt.tenant_id = NEW.tenant_id
      AND attempt.terminal_id = NEW.terminal_id
      AND attempt.external_reference = NEW.document_number
  ) THEN
    RAISE unique_violation
      USING MESSAGE = 'Terminal evidence was already used by a payment',
            CONSTRAINT = 'uq_pos_terminal_evidence_cross_type';
  END IF;
  RETURN NEW;
END;
$function$
"""


_REFUND_TRANSITION_FUNCTION_V2 = """
CREATE OR REPLACE FUNCTION public.trg_guard_pos_refund_attempt_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
  IF ROW(
    NEW.id, NEW.tenant_id, NEW.parent_sale_id, NEW.register_id,
    NEW.requested_by_user_id, NEW.operation_id, NEW.operation_hash,
    NEW.items_json, NEW.external_allocations_json, NEW.intent_version,
    NEW.reason_code, NEW.comment, NEW.total_amount, NEW.external_amount,
    NEW.currency, NEW.created_at, NEW.created_by
  ) IS DISTINCT FROM ROW(
    OLD.id, OLD.tenant_id, OLD.parent_sale_id, OLD.register_id,
    OLD.requested_by_user_id, OLD.operation_id, OLD.operation_hash,
    OLD.items_json, OLD.external_allocations_json, OLD.intent_version,
    OLD.reason_code, OLD.comment, OLD.total_amount, OLD.external_amount,
    OLD.currency, OLD.created_at, OLD.created_by
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


_REFUND_TRANSITION_FUNCTION_V1 = """
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


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.pos_refund_attempt
          ADD COLUMN intent_version SMALLINT NOT NULL DEFAULT 1,
          ADD COLUMN reason_code TEXT,
          ADD COLUMN comment TEXT,
          ADD CONSTRAINT ck_pos_refund_attempt_intent_version
            CHECK (intent_version IN (1, 2)),
          ADD CONSTRAINT ck_pos_refund_attempt_reason_code
            CHECK (
              reason_code IS NULL OR reason_code IN (
                'dispensing_error', 'duplicate_sale', 'pricing_error',
                'quality_issue', 'damaged_package', 'customer_cancelled', 'other'
              )
            ),
          ADD CONSTRAINT ck_pos_refund_attempt_comment
            CHECK (comment IS NULL OR char_length(comment) <= 500),
          ADD CONSTRAINT ck_pos_refund_attempt_intent_payload
            CHECK (
              (intent_version = 1 AND reason_code IS NULL AND comment IS NULL)
              OR intent_version = 2
            )
        """)
    # Rows predating this migration retain v1 compatibility. All inserts after
    # the migration commits fail closed into the intent-bound v2 format.
    op.execute("ALTER TABLE public.pos_refund_attempt " "ALTER COLUMN intent_version SET DEFAULT 2")
    op.execute(_REFUND_TRANSITION_FUNCTION_V2)
    # Existing rows must be clean before the invariant becomes active. Failing
    # the migration is safer than silently accepting ambiguous money evidence.
    op.execute("""
        DO $block$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.pos_payment_attempt AS attempt
            JOIN public.pos_refund_reference AS reference
              ON reference.tenant_id = attempt.tenant_id
             AND reference.terminal_id = attempt.terminal_id
             AND reference.document_number = attempt.external_reference
            WHERE attempt.terminal_id IS NOT NULL
              AND attempt.external_reference IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'Existing terminal evidence is shared by a payment and a refund';
          END IF;
        END
        $block$
        """)
    op.execute(_PAYMENT_GUARD)
    op.execute(_REFUND_GUARD)
    op.execute("""
        REVOKE ALL ON FUNCTION public.trg_guard_payment_evidence_cross_type()
          FROM PUBLIC, aurum_app, aurum_support
        """)
    op.execute("""
        REVOKE ALL ON FUNCTION public.trg_guard_refund_evidence_cross_type()
          FROM PUBLIC, aurum_app, aurum_support
        """)
    op.execute("""
        CREATE TRIGGER trg_payment_evidence_cross_type
          BEFORE UPDATE OF terminal_id, external_reference
          ON public.pos_payment_attempt
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_payment_evidence_cross_type()
        """)
    op.execute("""
        CREATE TRIGGER trg_refund_evidence_cross_type
          BEFORE INSERT ON public.pos_refund_reference
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_refund_evidence_cross_type()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_refund_evidence_cross_type " "ON public.pos_refund_reference"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_payment_evidence_cross_type " "ON public.pos_payment_attempt"
    )
    op.execute("DROP FUNCTION IF EXISTS public.trg_guard_refund_evidence_cross_type()")
    op.execute("DROP FUNCTION IF EXISTS public.trg_guard_payment_evidence_cross_type()")
    # Restore the exact pre-0132 transition contract before removing columns
    # referenced by the hardened function.
    op.execute(_REFUND_TRANSITION_FUNCTION_V1)
    op.execute("""
        ALTER TABLE public.pos_refund_attempt
          DROP CONSTRAINT IF EXISTS ck_pos_refund_attempt_intent_payload,
          DROP CONSTRAINT IF EXISTS ck_pos_refund_attempt_comment,
          DROP CONSTRAINT IF EXISTS ck_pos_refund_attempt_reason_code,
          DROP CONSTRAINT IF EXISTS ck_pos_refund_attempt_intent_version,
          DROP COLUMN IF EXISTS comment,
          DROP COLUMN IF EXISTS reason_code,
          DROP COLUMN IF EXISTS intent_version
        """)
