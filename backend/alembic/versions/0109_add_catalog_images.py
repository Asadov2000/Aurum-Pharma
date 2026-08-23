"""add optional catalog images

Revision ID: 0109
Revises: 0108
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0109"
down_revision: str | None = "0108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN image_version uuid")
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN image_width integer")
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN image_height integer")
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN image_size_bytes integer")
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN image_thumbnail_size_bytes integer")
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN image_sha256 text")
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN image_uploaded_at timestamptz")
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN image_uploaded_by uuid")
    op.execute("""
        ALTER TABLE public.tenant_catalog
          ADD CONSTRAINT ck_tc_image_metadata
          CHECK (
            (image_version IS NULL
             AND image_width IS NULL
             AND image_height IS NULL
             AND image_size_bytes IS NULL
             AND image_thumbnail_size_bytes IS NULL
             AND image_sha256 IS NULL
             AND image_uploaded_at IS NULL
             AND image_uploaded_by IS NULL)
            OR
            (image_version IS NOT NULL
             AND image_width > 0
             AND image_height > 0
             AND image_size_bytes > 0
             AND image_thumbnail_size_bytes > 0
             AND image_sha256 ~ '^[0-9a-f]{64}$'
             AND image_uploaded_at IS NOT NULL
             AND image_uploaded_by IS NOT NULL)
          )
        """)
    op.execute("""
        CREATE INDEX ix_tc_tenant_without_image
          ON public.tenant_catalog (tenant_id, id)
          WHERE deleted_at IS NULL AND image_version IS NULL
        """)


def downgrade() -> None:
    op.execute("DROP INDEX public.ix_tc_tenant_without_image")
    op.execute("ALTER TABLE public.tenant_catalog DROP CONSTRAINT ck_tc_image_metadata")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN image_uploaded_by")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN image_uploaded_at")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN image_sha256")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN image_thumbnail_size_bytes")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN image_size_bytes")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN image_height")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN image_width")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN image_version")
