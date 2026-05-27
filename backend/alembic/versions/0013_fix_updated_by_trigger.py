"""fix: trg_set_updated_meta now tolerates tables without `updated_by`.

The original function in 0001 unconditionally assigns
`NEW.updated_by := current_app_user_id()` inside the UPDATE branch.
That works for every tenant-data table — they all have the column —
but fails for the `tenant` table itself, which has only updated_at
and no updated_by (tenants are not "owned" by a user the way a
branch or a register is).

Symptom: any PATCH /api/v1/admin/tenants/{id} returned 500 with
`record "new" has no field "updated_by"`. Discovered via the
tenant-edit Playwright spec.

Fix: wrap the assignment in BEGIN/EXCEPTION undefined_column → NULL,
so tables without the column simply skip the bookkeeping. All other
tables behave identically.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FIXED_BODY = """
CREATE OR REPLACE FUNCTION trg_set_updated_meta() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  IF TG_OP = 'UPDATE' AND current_app_user_id() IS NOT NULL THEN
    BEGIN
      NEW.updated_by := current_app_user_id();
    EXCEPTION WHEN undefined_column THEN
      -- The `tenant` table tracks updates without an updated_by column.
      -- Other tables fall through and get the value as before.
      NULL;
    END;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_ORIGINAL_BODY = """
CREATE OR REPLACE FUNCTION trg_set_updated_meta() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  IF TG_OP = 'UPDATE' AND current_app_user_id() IS NOT NULL THEN
    NEW.updated_by := current_app_user_id();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_FIXED_BODY)


def downgrade() -> None:
    op.execute(_ORIGINAL_BODY)
