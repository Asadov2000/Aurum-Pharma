"""Preserve catalog creation identity across retries, edits and archival.

Revision ID: 0145
Revises: 0144
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0145"
down_revision: str | None = "0144"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN operation_id UUID")
    op.execute("ALTER TABLE public.tenant_catalog ADD COLUMN create_request_fingerprint TEXT")
    op.execute("""
        ALTER TABLE public.tenant_catalog ADD CONSTRAINT ck_tc_create_request_metadata
        CHECK (
          (operation_id IS NULL AND create_request_fingerprint IS NULL) OR
          (operation_id IS NOT NULL AND create_request_fingerprint IS NOT NULL
            AND create_request_fingerprint ~ '^[0-9a-f]{64}$')
        )
    """)
    op.execute("""
        ALTER TABLE public.tenant_catalog ADD CONSTRAINT uq_tenant_catalog_operation
        UNIQUE (tenant_id, operation_id)
    """)
    op.execute("""
        CREATE FUNCTION public.trg_guard_catalog_creation_metadata() RETURNS TRIGGER
        LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.operation_id IS NOT NULL THEN
              RAISE EXCEPTION 'Catalog creation identity cannot be deleted'
                USING ERRCODE='check_violation';
            END IF;
            RETURN OLD;
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
            OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
            OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
            OR NEW.create_request_fingerprint IS DISTINCT FROM OLD.create_request_fingerprint
          THEN
            RAISE EXCEPTION 'Catalog creation identity cannot change'
              USING ERRCODE='check_violation';
          END IF;
          RETURN NEW;
        END;
        $$
    """)
    op.execute(
        "ALTER FUNCTION public.trg_guard_catalog_creation_metadata() OWNER TO aurum_schema_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_catalog_creation_metadata() "
        "FROM PUBLIC,aurum_app,aurum_support"
    )
    op.execute("""
        CREATE TRIGGER guard_catalog_creation_metadata
        BEFORE UPDATE OR DELETE ON public.tenant_catalog
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_catalog_creation_metadata()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER guard_catalog_creation_metadata ON public.tenant_catalog")
    op.execute("DROP FUNCTION public.trg_guard_catalog_creation_metadata()")
    op.execute("ALTER TABLE public.tenant_catalog DROP CONSTRAINT uq_tenant_catalog_operation")
    op.execute("ALTER TABLE public.tenant_catalog DROP CONSTRAINT ck_tc_create_request_metadata")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN create_request_fingerprint")
    op.execute("ALTER TABLE public.tenant_catalog DROP COLUMN operation_id")
