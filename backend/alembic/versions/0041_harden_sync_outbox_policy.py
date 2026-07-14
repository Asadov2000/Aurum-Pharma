"""sync: harden outbox tenant policy on already-upgraded databases

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_tenant_policy() -> None:
    op.execute("""
        CREATE POLICY tenant_isolation ON public.sync_outbox
        USING (tenant_id = public.current_tenant_id())
        WITH CHECK (tenant_id = public.current_tenant_id())
        """)


def upgrade() -> None:
    # 0040 may already be installed locally with the former support predicate.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.sync_outbox")
    _create_tenant_policy()


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.sync_outbox")
    _create_tenant_policy()
