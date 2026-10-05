"""Allow optional catalog blister size without changing operational packaging.

Revision ID: 0173
Revises: 0172
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0173"
down_revision: str | Sequence[str] | None = "0172"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_PREPARATION_CHECK = r"""
unit_sale_preparation IS NULL OR COALESCE(
  CASE WHEN jsonb_typeof(unit_sale_preparation) = 'object'
    AND unit_sale_preparation ?& ARRAY[
      'version', 'state', 'unit', 'units_per_pack', 'dispense_step', 'unit_price'
    ]
    AND unit_sale_preparation - ARRAY[
      'version', 'state', 'unit', 'units_per_pack', 'dispense_step', 'unit_price'
    ] = '{}'::jsonb
  THEN CASE WHEN
    jsonb_typeof(unit_sale_preparation -> 'version') = 'number'
    AND unit_sale_preparation ->> 'version' = '1'
    AND jsonb_typeof(unit_sale_preparation -> 'state') = 'string'
    AND unit_sale_preparation ->> 'state' = 'prepared'
    AND jsonb_typeof(unit_sale_preparation -> 'unit') = 'string'
    AND unit_sale_preparation ->> 'unit' IN (
      'tablet', 'capsule', 'ampoule', 'sachet', 'suppository'
    )
    AND jsonb_typeof(unit_sale_preparation -> 'units_per_pack') = 'number'
    AND unit_sale_preparation ->> 'units_per_pack' ~ '^[0-9]{1,5}$'
    AND jsonb_typeof(unit_sale_preparation -> 'dispense_step') = 'number'
    AND unit_sale_preparation ->> 'dispense_step' ~ '^[0-9]{1,5}$'
    AND jsonb_typeof(unit_sale_preparation -> 'unit_price') = 'string'
    AND unit_sale_preparation ->> 'unit_price' ~ '^[0-9]{1,12}\.[0-9]{2}$'
  THEN CASE WHEN
    (unit_sale_preparation ->> 'units_per_pack')::integer BETWEEN 2 AND 10000
    AND (unit_sale_preparation ->> 'dispense_step')::integer BETWEEN 1 AND 10000
    AND (unit_sale_preparation ->> 'unit_price')::numeric > 0
  THEN (unit_sale_preparation ->> 'units_per_pack')::integer
    % (unit_sale_preparation ->> 'dispense_step')::integer = 0
  ELSE false END
  ELSE false END
  ELSE false END,
  false
)
"""

PREPARATION_CHECK = r"""
unit_sale_preparation IS NULL OR COALESCE(
  CASE WHEN jsonb_typeof(unit_sale_preparation) = 'object'
    AND unit_sale_preparation ?& ARRAY[
      'version', 'state', 'unit', 'units_per_pack', 'dispense_step', 'unit_price'
    ]
    AND unit_sale_preparation - ARRAY[
      'version', 'state', 'unit', 'units_per_pack', 'dispense_step', 'unit_price',
      'units_per_blister'
    ] = '{}'::jsonb
  THEN CASE WHEN
    jsonb_typeof(unit_sale_preparation -> 'version') = 'number'
    AND unit_sale_preparation ->> 'version' = '1'
    AND jsonb_typeof(unit_sale_preparation -> 'state') = 'string'
    AND unit_sale_preparation ->> 'state' = 'prepared'
    AND jsonb_typeof(unit_sale_preparation -> 'unit') = 'string'
    AND unit_sale_preparation ->> 'unit' IN (
      'tablet', 'capsule', 'ampoule', 'sachet', 'suppository'
    )
    AND jsonb_typeof(unit_sale_preparation -> 'units_per_pack') = 'number'
    AND unit_sale_preparation ->> 'units_per_pack' ~ '^[0-9]{1,5}$'
    AND jsonb_typeof(unit_sale_preparation -> 'dispense_step') = 'number'
    AND unit_sale_preparation ->> 'dispense_step' ~ '^[0-9]{1,5}$'
    AND jsonb_typeof(unit_sale_preparation -> 'unit_price') = 'string'
    AND unit_sale_preparation ->> 'unit_price' ~ '^[0-9]{1,12}\.[0-9]{2}$'
  THEN CASE WHEN
    (unit_sale_preparation ->> 'units_per_pack')::integer BETWEEN 2 AND 10000
    AND (unit_sale_preparation ->> 'dispense_step')::integer BETWEEN 1 AND 10000
    AND (unit_sale_preparation ->> 'unit_price')::numeric > 0
  THEN (unit_sale_preparation ->> 'units_per_pack')::integer
    % (unit_sale_preparation ->> 'dispense_step')::integer = 0
  ELSE false END
  ELSE false END
  ELSE false END,
  false
)
"""

BLISTER_CHECK = r"""
NOT (unit_sale_preparation ? 'units_per_blister') OR COALESCE(
  CASE WHEN jsonb_typeof(unit_sale_preparation -> 'units_per_blister') = 'number'
    AND unit_sale_preparation ->> 'units_per_blister' ~ '^[0-9]{1,5}$'
    AND jsonb_typeof(unit_sale_preparation -> 'units_per_pack') = 'number'
    AND unit_sale_preparation ->> 'units_per_pack' ~ '^[0-9]{1,5}$'
  THEN CASE WHEN
    (unit_sale_preparation ->> 'units_per_blister')::integer BETWEEN 2 AND 10000
  THEN (unit_sale_preparation ->> 'units_per_pack')::integer
    % (unit_sale_preparation ->> 'units_per_blister')::integer = 0
  ELSE false END
  ELSE false END,
  false
)
"""


def upgrade() -> None:
    op.drop_constraint("ck_tc_unit_sale_preparation", "tenant_catalog", type_="check")
    op.create_check_constraint(
        "ck_tc_unit_sale_preparation",
        "tenant_catalog",
        f"({PREPARATION_CHECK}) AND ({BLISTER_CHECK})",
    )


def downgrade() -> None:
    # Never discard recorded catalog configuration implicitly during a rollback.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM tenant_catalog
            WHERE unit_sale_preparation ? 'units_per_blister'
          ) THEN
            RAISE EXCEPTION 'Clear catalog units_per_blister before downgrading 0173';
          END IF;
        END $$
        """)
    op.drop_constraint("ck_tc_unit_sale_preparation", "tenant_catalog", type_="check")
    op.create_check_constraint(
        "ck_tc_unit_sale_preparation", "tenant_catalog", LEGACY_PREPARATION_CHECK
    )
