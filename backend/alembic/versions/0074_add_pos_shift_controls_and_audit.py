"""add explicit shift management permission and complete POS audit coverage

Revision ID: 0074
Revises: 0073
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0074"
down_revision: str | Sequence[str] | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_CODE = "pos.manage_shifts"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO public.permission (
          code,
          group_code,
          name,
          description,
          min_level_required,
          is_dangerous,
          is_active,
          scope_type,
          target_role_type,
          risk_level,
          developer_grantable,
          administrator_grantable,
          owner_grantable,
          developer_delegable,
          administrator_delegable,
          owner_delegable,
          requires_step_up,
          requires_confirmation
        )
        VALUES (
          '{PERMISSION_CODE}',
          'pos',
          'Управление сменами кассиров',
          'Просмотр и закрытие открытых смен других кассиров.',
          3,
          true,
          true,
          'BRANCH_SET',
          'tenant',
          'sensitive',
          true,
          true,
          true,
          true,
          true,
          true,
          false,
          true
        )
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO public.role_permission (role_id, permission_code)
        SELECT role.id, '{PERMISSION_CODE}'
        FROM public.role
        WHERE role.is_system = true
          AND role.level <= 3
        ON CONFLICT (role_id, permission_code) DO NOTHING
        """
    )

    op.execute("LOCK TABLE public.shift IN SHARE ROW EXCLUSIVE MODE")
    op.execute("LOCK TABLE public.batch_movement IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        CREATE TRIGGER trg_audit_shift
          AFTER INSERT OR UPDATE OR DELETE ON public.shift
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_batch_movement
          AFTER INSERT ON public.batch_movement
          FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_batch_movement ON public.batch_movement"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_audit_shift ON public.shift")
    op.execute(
        f"DELETE FROM public.role_permission WHERE permission_code = '{PERMISSION_CODE}'"
    )
    op.execute(f"DELETE FROM public.permission WHERE code = '{PERMISSION_CODE}'")
