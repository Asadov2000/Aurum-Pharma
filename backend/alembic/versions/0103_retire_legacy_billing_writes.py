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


def _grant_missing_tenant_reference_privilege() -> None:
    op.execute("""
        CREATE TEMPORARY TABLE aurum_0103_missing_reference_privilege (
          table_name TEXT PRIMARY KEY
        ) ON COMMIT DROP
        """)
    op.execute("""
        DO $$
        BEGIN
          IF NOT pg_catalog.has_table_privilege(
            'aurum_schema_owner', 'public.tenant', 'REFERENCES'
          ) THEN
            INSERT INTO pg_temp.aurum_0103_missing_reference_privilege (
              table_name
            ) VALUES ('tenant');
            GRANT REFERENCES ON TABLE public.tenant TO aurum_schema_owner;
          END IF;
        END
        $$
        """)


def _restore_tenant_reference_privilege() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_temp.aurum_0103_missing_reference_privilege
            WHERE table_name = 'tenant'
          ) THEN
            REVOKE REFERENCES ON TABLE public.tenant FROM aurum_schema_owner;
          END IF;
        END
        $$
        """)
    op.execute("DROP TABLE pg_temp.aurum_0103_missing_reference_privilege")


def upgrade() -> None:
    _grant_missing_tenant_reference_privilege()
    for table in LEGACY_BILLING_TABLES:
        op.execute(f"ALTER TABLE public.{table} OWNER TO aurum_schema_owner")
        _revoke_runtime_access(table)
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app, aurum_support")
        _replace_tenant_foreign_key(table=table, on_delete="RESTRICT")
    _restore_tenant_reference_privilege()


def downgrade() -> None:
    _grant_missing_tenant_reference_privilege()
    for table in LEGACY_BILLING_TABLES:
        _replace_tenant_foreign_key(table=table, on_delete="CASCADE")
        _revoke_runtime_access(table)
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table} "
            "TO aurum_app, aurum_support"
        )
    _restore_tenant_reference_privilege()
