"""add explicit permission for managing another cashier's active sale

Revision ID: 0072
Revises: 0071
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0072"
down_revision: str | Sequence[str] | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CODE = "pos.manage_sales"


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
          '{_CODE}',
          'pos',
          'Управление продажами кассиров',
          'Изменение и завершение незакрытых продаж других кассиров.',
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
        SELECT role.id, '{_CODE}'
        FROM public.role
        WHERE role.is_system = true
          AND role.level <= 3
        ON CONFLICT (role_id, permission_code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM public.role_permission WHERE permission_code = '{_CODE}'")
    op.execute(f"DELETE FROM public.permission WHERE code = '{_CODE}'")
