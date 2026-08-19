"""retire legacy billing writes

Revision ID: 0103
Revises: 0102
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0103"
down_revision: str | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_BILLING_TABLES = ("invoice", "payment")
RUNTIME_ROLES = (
    "PUBLIC",
    "aurum_app",
    "aurum_support",
    "aurum_mailer",
    "aurum_edge_cash_executor",
    "aurum_edge_cash_owner",
)


def _revoke_runtime_access(table: str) -> None:
    grantees = ", ".join(RUNTIME_ROLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM {grantees}")


def _replace_tenant_foreign_key(*, table: str, on_delete: str) -> None:
    constraint = f"{table}_tenant_id_fkey"
    op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}")
    op.execute(
        f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} "
        f"FOREIGN KEY (tenant_id) REFERENCES public.tenant(id) ON DELETE {on_delete}"
    )


def upgrade() -> None:
    for table in LEGACY_BILLING_TABLES:
        op.execute(f"ALTER TABLE public.{table} OWNER TO aurum_schema_owner")
        _revoke_runtime_access(table)
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app, aurum_support")
        _replace_tenant_foreign_key(table=table, on_delete="RESTRICT")


def downgrade() -> None:
    for table in LEGACY_BILLING_TABLES:
        _replace_tenant_foreign_key(table=table, on_delete="CASCADE")
        _revoke_runtime_access(table)
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table} "
            "TO aurum_app, aurum_support"
        )
