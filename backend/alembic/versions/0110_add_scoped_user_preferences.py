"""add scoped user preferences and version pharmacy settings

Revision ID: 0110
Revises: 0109
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0110"
down_revision: str | None = "0109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.user_preferences (
          user_id UUID PRIMARY KEY REFERENCES public.app_user(id) ON DELETE CASCADE,
          theme TEXT NOT NULL DEFAULT 'system'
            CONSTRAINT ck_user_preferences_theme CHECK (theme IN ('system','light','dark')),
          density TEXT NOT NULL DEFAULT 'comfortable'
            CONSTRAINT ck_user_preferences_density
              CHECK (density IN ('auto','compact','comfortable','touch')),
          contrast TEXT NOT NULL DEFAULT 'standard'
            CONSTRAINT ck_user_preferences_contrast CHECK (contrast IN ('standard','high')),
          reduce_motion BOOLEAN NOT NULL DEFAULT false,
          accent TEXT NOT NULL DEFAULT 'teal'
            CONSTRAINT ck_user_preferences_accent
              CHECK (accent IN ('teal','blue','violet','green','amber','rose')),
          workspace_preferences JSONB NOT NULL DEFAULT '{}'::jsonb
            CONSTRAINT ck_user_preferences_workspaces_object
              CHECK (jsonb_typeof(workspace_preferences) = 'object')
            CONSTRAINT ck_user_preferences_workspaces_size
              CHECK (pg_column_size(workspace_preferences) <= 65536),
          version INTEGER NOT NULL DEFAULT 1
            CONSTRAINT ck_user_preferences_version CHECK (version >= 1),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    op.execute("""
        CREATE TRIGGER trg_user_preferences_updated
          BEFORE UPDATE ON public.user_preferences
          FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
        """)
    op.execute("ALTER TABLE public.user_preferences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.user_preferences FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY user_preferences_self ON public.user_preferences
          USING (user_id = public.current_app_user_id())
          WITH CHECK (user_id = public.current_app_user_id())
        """)
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.user_preferences "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE public.user_preferences "
        "TO aurum_app, aurum_support"
    )

    op.execute("""
        ALTER TABLE public.tenant_settings
          ADD COLUMN version INTEGER NOT NULL DEFAULT 1,
          ADD CONSTRAINT ck_tenant_settings_version CHECK (version >= 1)
        """)

    # The capability remains on the protected owner template for backwards
    # compatibility, but it disappears from every role constructor.
    op.execute("""
        UPDATE public.permission
        SET
          developer_grantable = false,
          administrator_grantable = false,
          owner_grantable = false,
          developer_delegable = false,
          administrator_delegable = false,
          owner_delegable = false
        WHERE code = 'settings.update'
        """)


def downgrade() -> None:
    op.execute("""
        UPDATE public.permission
        SET
          developer_grantable = true,
          administrator_grantable = true,
          owner_grantable = true,
          developer_delegable = true,
          administrator_delegable = true,
          owner_delegable = true
        WHERE code = 'settings.update'
        """)
    op.execute("ALTER TABLE public.tenant_settings DROP CONSTRAINT ck_tenant_settings_version")
    op.execute("ALTER TABLE public.tenant_settings DROP COLUMN version")
    op.execute("DROP TABLE public.user_preferences")
