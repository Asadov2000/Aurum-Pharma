"""complete owner payment management and index reconciliation queue

Revision ID: 0125
Revises: 0124
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0125"
down_revision: str | Sequence[str] | None = "0124"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO public.role_template_permission (template_id, permission_code)
        SELECT template.id, 'pos.manage_sales'
        FROM public.role_template AS template
        WHERE template.slug = 'owner' AND template.is_active
        ON CONFLICT (template_id, permission_code) DO NOTHING
        """)
    op.execute(
        "ALTER TABLE public.role_permission " "DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        INSERT INTO public.role_permission (role_id, permission_code)
        SELECT role.id, 'pos.manage_sales'
        FROM public.role
        WHERE role.is_protected = true
          AND role.protected_kind = 'tenant_owner'
          AND role.is_active = true
        ON CONFLICT (role_id, permission_code) DO NOTHING
        """)
    op.execute(
        "ALTER TABLE public.role_permission " "ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        INSERT INTO public.access_role_version_permission (
          role_version_id,
          permission_code,
          created_at
        )
        SELECT version.id, 'pos.manage_sales', pg_catalog.statement_timestamp()
        FROM public.access_role_version AS version
        JOIN public.role AS role ON role.id = version.role_id
        WHERE version.status = 'published'
          AND role.is_protected = true
          AND role.protected_kind = 'tenant_owner'
          AND role.is_active = true
        ON CONFLICT (role_version_id, permission_code) DO NOTHING
        """)
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")
    op.execute("""
        CREATE INDEX ix_pos_payment_attempt_reconciliation_queue
          ON public.pos_payment_attempt (
            tenant_id,
            status,
            reconciliation_started_at,
            id
          )
          WHERE status IN ('requires_reconciliation', 'confirmed')
        """)


def downgrade() -> None:
    op.execute("DROP INDEX public.ix_pos_payment_attempt_reconciliation_queue")
    op.execute("""
        DELETE FROM public.access_role_version_permission AS version_permission
        USING public.access_role_version AS version, public.role AS role
        WHERE version_permission.role_version_id = version.id
          AND version.role_id = role.id
          AND version_permission.permission_code = 'pos.manage_sales'
          AND role.is_protected = true
          AND role.protected_kind = 'tenant_owner'
        """)
    op.execute(
        "ALTER TABLE public.role_permission " "DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        DELETE FROM public.role_permission AS role_permission
        USING public.role AS role
        WHERE role_permission.role_id = role.id
          AND role_permission.permission_code = 'pos.manage_sales'
          AND role.is_protected = true
          AND role.protected_kind = 'tenant_owner'
        """)
    op.execute(
        "ALTER TABLE public.role_permission " "ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        DELETE FROM public.role_template_permission AS template_permission
        USING public.role_template AS template
        WHERE template_permission.template_id = template.id
          AND template_permission.permission_code = 'pos.manage_sales'
          AND template.slug = 'owner'
        """)
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")
