"""Performance indexes (pre-pilot): barcode(tenant_id, code) + shift(tenant_id, status).

- barcode: the cashier's barcode scan (find_item_by_barcode → WHERE code = …)
  had no index on `code`, so every scan was a seq scan. With a real catalogue
  of thousands of barcodes that's a delay on every beep. Indexed as
  (tenant_id, code) so RLS's injected tenant_id predicate leads the index.
  NOT unique — we don't want to block a legitimate import; barcode uniqueness
  is a separate question.
- shift: the dashboard counts open/closed shifts by tenant_id, which had no
  supporting index → seq scan on shift.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_barcode_tenant_code ON barcode (tenant_id, code)")
    op.execute("CREATE INDEX ix_shift_tenant ON shift (tenant_id, status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_shift_tenant")
    op.execute("DROP INDEX IF EXISTS ix_barcode_tenant_code")
