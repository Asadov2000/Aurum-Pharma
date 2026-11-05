"""Add validated preparation metadata without changing stock or enabling unit sales.

Revision ID: 0161
Revises: 0160
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0161"
down_revision: str | Sequence[str] | None = "0160"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREPARATION_CHECK = r"""
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


def upgrade() -> None:
    op.add_column(
        "tenant_catalog",
        sa.Column("unit_sale_preparation", JSONB(none_as_null=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_tc_unit_sale_preparation", "tenant_catalog", PREPARATION_CHECK
    )


def downgrade() -> None:
    # This revision only stores preparation: no stock/sale quantities were converted.
    op.drop_constraint("ck_tc_unit_sale_preparation", "tenant_catalog", type_="check")
    op.drop_column("tenant_catalog", "unit_sale_preparation")
