"""scope incoming document suppliers to their tenant

Revision ID: 0126
Revises: 0125
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0126"
down_revision: str | Sequence[str] | None = "0125"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("LOCK TABLE public.supplier, public.incoming_document IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.incoming_document AS document
            LEFT JOIN public.supplier AS supplier
              ON supplier.tenant_id = document.tenant_id
             AND supplier.id = document.supplier_id
            WHERE supplier.id IS NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot scope incoming suppliers: cross-tenant or missing supplier reference exists';
          END IF;
        END;
        $$;
        """)
    op.create_foreign_key(
        "fk_incoming_document_tenant_supplier",
        "incoming_document",
        "supplier",
        ["tenant_id", "supplier_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_incoming_document_tenant_supplier",
        "incoming_document",
        type_="foreignkey",
    )
