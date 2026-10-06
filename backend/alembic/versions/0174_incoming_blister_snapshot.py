"""Freeze optional blister composition without activating physical stock v2.

Revision ID: 0174
Revises: 0173
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0174"
down_revision: str | Sequence[str] | None = "0173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SNAPSHOT_CHECK = r"""
unit_packaging_snapshot_v2 IS NULL OR COALESCE(
  CASE WHEN jsonb_typeof(unit_packaging_snapshot_v2) = 'object'
    AND unit_packaging_snapshot_v2 ?& ARRAY[
      'version', 'unit', 'units_per_pack', 'units_per_blister', 'dispense_step'
    ]
    AND unit_packaging_snapshot_v2 - ARRAY[
      'version', 'unit', 'units_per_pack', 'units_per_blister', 'dispense_step'
    ] = '{}'::jsonb
    AND unit_packaging_preparation IS NOT NULL
  THEN CASE WHEN
    jsonb_typeof(unit_packaging_snapshot_v2 -> 'version') = 'number'
    AND unit_packaging_snapshot_v2 ->> 'version' = '2'
    AND (unit_packaging_snapshot_v2 - ARRAY['version', 'units_per_blister'])
      = (unit_packaging_preparation - 'version')
    AND jsonb_typeof(unit_packaging_snapshot_v2 -> 'unit') = 'string'
    AND unit_packaging_snapshot_v2 ->> 'unit' IN (
      'tablet', 'capsule', 'ampoule', 'sachet', 'suppository'
    )
    AND jsonb_typeof(unit_packaging_snapshot_v2 -> 'units_per_pack') = 'number'
    AND unit_packaging_snapshot_v2 ->> 'units_per_pack' ~ '^[0-9]{1,5}$'
    AND jsonb_typeof(unit_packaging_snapshot_v2 -> 'dispense_step') = 'number'
    AND unit_packaging_snapshot_v2 ->> 'dispense_step' ~ '^[0-9]{1,5}$'
  THEN CASE WHEN
    (unit_packaging_snapshot_v2 ->> 'units_per_pack')::integer BETWEEN 2 AND 10000
    AND (unit_packaging_snapshot_v2 ->> 'dispense_step')::integer BETWEEN 1 AND 10000
  THEN
    (unit_packaging_snapshot_v2 ->> 'units_per_pack')::integer
      % (unit_packaging_snapshot_v2 ->> 'dispense_step')::integer = 0
    AND CASE WHEN unit_packaging_snapshot_v2 -> 'units_per_blister' = 'null'::jsonb
      THEN true
      WHEN jsonb_typeof(unit_packaging_snapshot_v2 -> 'units_per_blister') = 'number'
        AND unit_packaging_snapshot_v2 ->> 'units_per_blister' ~ '^[0-9]{1,5}$'
      THEN CASE WHEN
        (unit_packaging_snapshot_v2 ->> 'units_per_blister')::integer BETWEEN 2 AND 10000
        THEN (unit_packaging_snapshot_v2 ->> 'units_per_pack')::integer
          % (unit_packaging_snapshot_v2 ->> 'units_per_blister')::integer = 0
        ELSE false END
      ELSE false END
  ELSE false END
  ELSE false END
  ELSE false END,
  false
)
"""

BATCH_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_batch_unit_snapshot_v2()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.unit_packaging_snapshot_v2 IS DISTINCT FROM OLD.unit_packaging_snapshot_v2 THEN
    RAISE EXCEPTION 'Batch blister snapshot is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$
"""

ACCEPTANCE_GUARD_SQL = """
CREATE FUNCTION public.trg_validate_incoming_unit_snapshot_v2()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.status = 'accepted' AND EXISTS (
    SELECT 1 FROM public.incoming_item AS item
    LEFT JOIN public.batch AS batch
      ON batch.tenant_id = item.tenant_id AND batch.id = item.created_batch_id
    LEFT JOIN public.unit_stock_lot AS lot
      ON lot.tenant_id = item.tenant_id AND lot.id = item.created_unit_lot_id
    WHERE item.tenant_id = NEW.tenant_id AND item.document_id = NEW.id
      AND (
        (item.created_unit_lot_id IS NULL AND (
          batch.id IS NULL OR item.unit_packaging_snapshot_v2
            IS DISTINCT FROM batch.unit_packaging_snapshot_v2
        ))
        OR (item.created_unit_lot_id IS NOT NULL
          AND item.unit_packaging_snapshot_v2 IS NOT NULL AND (
            lot.id IS NULL OR
            (item.unit_packaging_snapshot_v2 - ARRAY['version', 'units_per_blister'])
              IS DISTINCT FROM jsonb_build_object(
                'unit', lot.unit, 'units_per_pack', lot.units_per_pack,
                'dispense_step', lot.dispense_step
              )
          ))
      )
  ) THEN
    RAISE EXCEPTION 'Incoming blister snapshot has inconsistent provenance'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    # Existing item lifecycle guards lock the parent and protect every accepted field.
    # Keep the separate v2 acceptance guard: 0166 can return early from the v1 guard.
    for table, constraint in (
        ("incoming_item", "ck_ii_unit_packaging_snapshot_v2"),
        ("batch", "ck_batch_unit_packaging_snapshot_v2"),
    ):
        op.add_column(
            table,
            sa.Column("unit_packaging_snapshot_v2", JSONB(none_as_null=True), nullable=True),
        )
        op.create_check_constraint(constraint, table, SNAPSHOT_CHECK)
    op.execute(BATCH_GUARD_SQL)
    op.execute(ACCEPTANCE_GUARD_SQL)
    for function in (
        "trg_guard_batch_unit_snapshot_v2",
        "trg_validate_incoming_unit_snapshot_v2",
    ):
        op.execute(f"ALTER FUNCTION public.{function}() OWNER TO aurum_schema_owner")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC, aurum_app, aurum_support"
        )
    op.execute("""
        CREATE TRIGGER trg_guard_batch_unit_snapshot_v2 BEFORE UPDATE ON public.batch
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_batch_unit_snapshot_v2()
        """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_validate_incoming_unit_snapshot_v2
        AFTER INSERT OR UPDATE ON public.incoming_document
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.trg_validate_incoming_unit_snapshot_v2()
        """)


def downgrade() -> None:
    op.execute("LOCK TABLE public.incoming_item, public.batch IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM public.incoming_item
            WHERE unit_packaging_snapshot_v2 IS NOT NULL)
            OR EXISTS (SELECT 1 FROM public.batch
              WHERE unit_packaging_snapshot_v2 IS NOT NULL) THEN
            RAISE EXCEPTION 'Blister snapshot downgrade would lose history'
              USING ERRCODE = '55000';
          END IF;
        END $$
        """)
    op.execute("DROP TRIGGER trg_validate_incoming_unit_snapshot_v2 ON public.incoming_document")
    op.execute("DROP TRIGGER trg_guard_batch_unit_snapshot_v2 ON public.batch")
    op.execute("DROP FUNCTION public.trg_validate_incoming_unit_snapshot_v2()")
    op.execute("DROP FUNCTION public.trg_guard_batch_unit_snapshot_v2()")
    for table, constraint in (
        ("batch", "ck_batch_unit_packaging_snapshot_v2"),
        ("incoming_item", "ck_ii_unit_packaging_snapshot_v2"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.drop_column(table, "unit_packaging_snapshot_v2")
