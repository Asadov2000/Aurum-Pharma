"""add tenant-isolated personal POS favorites

Revision ID: 0080
Revises: 0079
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0080"
down_revision: str | Sequence[str] | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_tenant_catalog_tenant_id_id",
        "tenant_catalog",
        ["tenant_id", "id"],
    )
    op.execute("""
        CREATE TABLE public.pos_favorite (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id   UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          user_id     UUID NOT NULL REFERENCES public.app_user(id) ON DELETE CASCADE,
          catalog_id  UUID NOT NULL,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by  UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_by  UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_pos_favorite_tenant_user_catalog
            UNIQUE (tenant_id, user_id, catalog_id),
          CONSTRAINT fk_pos_favorite_tenant_catalog
            FOREIGN KEY (tenant_id, catalog_id)
            REFERENCES public.tenant_catalog(tenant_id, id)
            ON DELETE CASCADE
        )
        """)
    op.execute(
        "CREATE INDEX ix_pos_favorite_owner_created "
        "ON public.pos_favorite (tenant_id, user_id, created_at DESC, id DESC)"
    )

    op.execute("ALTER TABLE public.pos_favorite ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.pos_favorite FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY pos_favorite_owner_access ON public.pos_favorite
          FOR ALL
          USING (
            tenant_id = public.current_tenant_id()
            AND user_id = public.current_app_user_id()
          )
          WITH CHECK (
            tenant_id = public.current_tenant_id()
            AND user_id = public.current_app_user_id()
          )
        """)

    op.execute("""
        CREATE TRIGGER trg_pos_favorite_created_meta
          BEFORE INSERT ON public.pos_favorite
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_pos_favorite_updated_meta
          BEFORE UPDATE ON public.pos_favorite
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_pos_favorite
          AFTER INSERT OR UPDATE OR DELETE ON public.pos_favorite
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.pos_favorite "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.pos_favorite "
        "TO aurum_app, aurum_support"
    )


def downgrade() -> None:
    op.execute("DROP TABLE public.pos_favorite")
    op.drop_constraint(
        "uq_tenant_catalog_tenant_id_id",
        "tenant_catalog",
        type_="unique",
    )
