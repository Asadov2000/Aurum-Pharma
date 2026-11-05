"""Freeze incoming physical packaging without converting or activating stock.

Revision ID: 0163
Revises: 0162
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0163"
down_revision: str | Sequence[str] | None = "0162"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREPARATION_CHECK = r"""
unit_packaging_preparation IS NULL OR COALESCE(
  CASE WHEN jsonb_typeof(unit_packaging_preparation) = 'object'
    AND unit_packaging_preparation ?& ARRAY[
      'version', 'unit', 'units_per_pack', 'dispense_step'
    ]
    AND unit_packaging_preparation - ARRAY[
      'version', 'unit', 'units_per_pack', 'dispense_step'
    ] = '{}'::jsonb
  THEN CASE WHEN
    jsonb_typeof(unit_packaging_preparation -> 'version') = 'number'
    AND unit_packaging_preparation ->> 'version' = '1'
    AND jsonb_typeof(unit_packaging_preparation -> 'unit') = 'string'
    AND unit_packaging_preparation ->> 'unit' IN (
      'tablet', 'capsule', 'ampoule', 'sachet', 'suppository'
    )
    AND jsonb_typeof(unit_packaging_preparation -> 'units_per_pack') = 'number'
    AND unit_packaging_preparation ->> 'units_per_pack' ~ '^[0-9]{1,5}$'
    AND jsonb_typeof(unit_packaging_preparation -> 'dispense_step') = 'number'
    AND unit_packaging_preparation ->> 'dispense_step' ~ '^[0-9]{1,5}$'
  THEN CASE WHEN
    (unit_packaging_preparation ->> 'units_per_pack')::integer BETWEEN 2 AND 10000
    AND (unit_packaging_preparation ->> 'dispense_step')::integer BETWEEN 1 AND 10000
  THEN
    (unit_packaging_preparation ->> 'units_per_pack')::integer
      % (unit_packaging_preparation ->> 'dispense_step')::integer = 0
    AND {quantity} > 0 AND {quantity} = trunc({quantity})
    AND {quantity} * (unit_packaging_preparation ->> 'units_per_pack')::integer
      <= 99999999999
  ELSE false END
  ELSE false END
  ELSE false END,
  false
)
"""


BATCH_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_batch_unit_packaging_preparation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF NEW.unit_packaging_preparation IS DISTINCT FROM OLD.unit_packaging_preparation THEN
    RAISE EXCEPTION 'Batch unit packaging preparation is immutable'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$
"""


ACCEPTANCE_GUARD_SQL = """
CREATE FUNCTION public.trg_validate_incoming_unit_packaging_preparation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF NEW.status = 'accepted' AND EXISTS (
    SELECT 1
    FROM public.incoming_item AS item
    LEFT JOIN public.batch AS batch
      ON batch.tenant_id = item.tenant_id AND batch.id = item.created_batch_id
    WHERE item.tenant_id = NEW.tenant_id AND item.document_id = NEW.id
      AND (batch.id IS NULL OR item.unit_packaging_preparation
        IS DISTINCT FROM batch.unit_packaging_preparation)
  ) THEN
    RAISE EXCEPTION 'Incoming unit packaging preparation has inconsistent provenance'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    for table, constraint, quantity in (
        ("incoming_item", "ck_ii_unit_packaging_preparation", "qty"),
        ("batch", "ck_batch_unit_packaging_preparation", "qty_initial"),
    ):
        op.add_column(
            table,
            sa.Column("unit_packaging_preparation", JSONB(none_as_null=True), nullable=True),
        )
        op.create_check_constraint(
            constraint, table, PREPARATION_CHECK.replace("{quantity}", quantity)
        )
    op.execute(BATCH_GUARD_SQL)
    op.execute(ACCEPTANCE_GUARD_SQL)
    for function in (
        "trg_guard_batch_unit_packaging_preparation",
        "trg_validate_incoming_unit_packaging_preparation",
    ):
        op.execute(f"ALTER FUNCTION public.{function}() OWNER TO aurum_schema_owner")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function}() " "FROM PUBLIC, aurum_app, aurum_support"
        )
    op.execute("""
        CREATE TRIGGER trg_guard_batch_unit_packaging_preparation
        BEFORE UPDATE ON public.batch
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_batch_unit_packaging_preparation()
        """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_validate_incoming_unit_packaging_preparation
        AFTER INSERT OR UPDATE ON public.incoming_document
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_validate_incoming_unit_packaging_preparation()
        """)


def downgrade() -> None:
    # Rollback is permitted only when no physical-packaging snapshots would be lost.
    op.execute("LOCK TABLE public.incoming_item, public.batch IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.incoming_item
            WHERE unit_packaging_preparation IS NOT NULL)
            OR EXISTS (SELECT 1 FROM public.batch
              WHERE unit_packaging_preparation IS NOT NULL) THEN
            RAISE EXCEPTION 'Unit packaging preparation downgrade would lose snapshots'
              USING ERRCODE = '55000';
          END IF;
        END $$
        """)
    op.execute(
        "DROP TRIGGER trg_validate_incoming_unit_packaging_preparation "
        "ON public.incoming_document"
    )
    op.execute("DROP TRIGGER trg_guard_batch_unit_packaging_preparation ON public.batch")
    op.execute("DROP FUNCTION public.trg_validate_incoming_unit_packaging_preparation()")
    op.execute("DROP FUNCTION public.trg_guard_batch_unit_packaging_preparation()")
    for table, constraint in (
        ("batch", "ck_batch_unit_packaging_preparation"),
        ("incoming_item", "ck_ii_unit_packaging_preparation"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.drop_column(table, "unit_packaging_preparation")
