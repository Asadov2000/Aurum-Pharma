"""add least-privilege platform billing capabilities

Revision ID: 0093
Revises: 0092
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PLATFORM_BILLING_PERMISSION_CODES = (
    "platform.billing.view",
    "platform.billing.payment.review",
    "platform.billing.payment.approve",
    "platform.billing.invoice.issue",
    "platform.billing.adjustment.create",
    "platform.billing.adjustment.approve",
    "platform.billing.plan.manage",
    "platform.billing.audit.view",
)


def upgrade() -> None:
    op.execute("""
        INSERT INTO public.permission (
          code, group_code, name, description, min_level_required,
          is_dangerous, is_active, scope_type, target_role_type, risk_level,
          developer_grantable, administrator_grantable, owner_grantable,
          developer_delegable, administrator_delegable, owner_delegable,
          requires_step_up, requires_confirmation
        ) VALUES
          ('platform.billing.view', 'platform_billing', 'Просмотр расчётов',
           'Просмотр сводки, подписок и счетов Aurum Pharma',
           2, false, true, 'PLATFORM', 'platform', 'sensitive',
           true, true, false, true, false, false, true, false),
          ('platform.billing.payment.review', 'platform_billing', 'Проверка заявлений об оплате',
           'Просмотр очереди и банковских подтверждений без решения по деньгам',
           2, false, true, 'PLATFORM', 'platform', 'sensitive',
           true, true, false, true, false, false, true, false),
          ('platform.billing.payment.approve', 'platform_billing', 'Подтверждение оплаты',
           'Подтверждение сверенного банковского платежа',
           2, true, true, 'PLATFORM', 'platform', 'critical',
           true, true, false, true, false, false, true, true),
          ('platform.billing.invoice.issue', 'platform_billing', 'Выпуск счетов',
           'Выпуск неизменяемых счетов из серверного расчёта',
           2, true, true, 'PLATFORM', 'platform', 'critical',
           true, true, false, true, false, false, true, true),
          ('platform.billing.adjustment.create', 'platform_billing', 'Создание корректировок',
           'Создание финансовой корректировки для отдельного утверждения',
           2, true, true, 'PLATFORM', 'platform', 'critical',
           true, true, false, true, false, false, true, true),
          ('platform.billing.adjustment.approve', 'platform_billing', 'Утверждение корректировок',
           'Утверждение корректировки, созданной другим сотрудником',
           2, true, true, 'PLATFORM', 'platform', 'critical',
           true, true, false, true, false, false, true, true),
          ('platform.billing.plan.manage', 'platform_billing', 'Управление тарифами',
           'Создание будущих версий цены и условий тарифа',
           2, true, true, 'PLATFORM', 'platform', 'critical',
           true, true, false, true, false, false, true, true),
          ('platform.billing.audit.view', 'platform_billing', 'Аудит расчётов',
           'Просмотр защищённой финансовой хронологии биллинга',
           2, false, true, 'PLATFORM', 'platform', 'critical',
           true, true, false, true, false, false, true, false)
        """)

    # Existing Developers receive the new capability envelope. Administrator
    # grants are intentionally unchanged: a Developer must explicitly replace
    # their grant with only the billing duties they actually need.
    op.execute(
        "ALTER TABLE public.platform_access_grant_permission "
        "DISABLE TRIGGER trg_10_guard_platform_access_grant_permission"
    )
    op.execute("""
        INSERT INTO public.platform_access_grant_permission (
          grant_id, permission_code, created_by
        )
        SELECT grant_row.id, permission.code, grant_row.requested_by
        FROM public.platform_access_grant AS grant_row
        CROSS JOIN public.permission AS permission
        WHERE grant_row.access_kind = 'developer'
          AND grant_row.status = 'active'
          AND permission.code = ANY(ARRAY[
            'platform.billing.view',
            'platform.billing.payment.review',
            'platform.billing.payment.approve',
            'platform.billing.invoice.issue',
            'platform.billing.adjustment.create',
            'platform.billing.adjustment.approve',
            'platform.billing.plan.manage',
            'platform.billing.audit.view'
          ])
        ON CONFLICT DO NOTHING
        """)
    op.execute(
        "ALTER TABLE public.platform_access_grant_permission "
        "ENABLE TRIGGER trg_10_guard_platform_access_grant_permission"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.platform_access_grant_permission "
        "DISABLE TRIGGER trg_10_guard_platform_access_grant_permission"
    )
    codes = ", ".join(f"'{code}'" for code in PLATFORM_BILLING_PERMISSION_CODES)
    op.execute(
        "DELETE FROM public.platform_access_grant_permission "
        f"WHERE permission_code IN ({codes})"
    )
    op.execute(
        "ALTER TABLE public.platform_access_grant_permission "
        "ENABLE TRIGGER trg_10_guard_platform_access_grant_permission"
    )
    op.execute(f"DELETE FROM public.permission WHERE code IN ({codes})")
