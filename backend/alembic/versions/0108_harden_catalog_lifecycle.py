"""harden catalog lifecycle and tenant consistency

Revision ID: 0108
Revises: 0107
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0108"
down_revision: str | None = "0107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.tenant_catalog
          ADD CONSTRAINT ck_tc_base_price
          CHECK (base_price IS NULL OR base_price >= 0) NOT VALID
        """)
    op.execute("""
        ALTER TABLE public.tenant_catalog
          VALIDATE CONSTRAINT ck_tc_base_price
        """)
    op.execute("""
        CREATE INDEX ix_tc_tenant_brand_id_active
          ON public.tenant_catalog (tenant_id, brand_name, id)
          WHERE deleted_at IS NULL AND is_active
        """)
    op.execute("""
        ALTER TABLE public.barcode
          ADD CONSTRAINT fk_barcode_tenant_catalog
          FOREIGN KEY (tenant_id, catalog_id)
          REFERENCES public.tenant_catalog (tenant_id, id)
          ON DELETE CASCADE NOT VALID
        """)
    op.execute("""
        ALTER TABLE public.barcode
          VALIDATE CONSTRAINT fk_barcode_tenant_catalog
        """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.barcode
          DROP CONSTRAINT fk_barcode_tenant_catalog
        """)
    op.execute("DROP INDEX public.ix_tc_tenant_brand_id_active")
    op.execute("""
        ALTER TABLE public.tenant_catalog
          DROP CONSTRAINT ck_tc_base_price
        """)
