"""Migration seed: permissions catalogue and system roles."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.roles.models import (
    Permission,
    Role,
    RolePermission,
    RoleTemplate,
    RoleTemplatePermission,
)

GRANT_SCOPED_PLATFORM_CODES = {
    "platform.tenants.view",
    "platform.tenants.manage",
    "platform.memberships.manage",
    "platform.ownership.provision",
    "platform.billing.manage",
    "platform.billing.view",
    "platform.billing.payment.review",
    "platform.billing.payment.approve",
    "platform.billing.invoice.issue",
    "platform.billing.adjustment.create",
    "platform.billing.adjustment.approve",
    "platform.billing.plan.manage",
    "platform.billing.audit.view",
    "platform.support.use",
    "platform.sync.view",
    "platform.sync.manage",
    "platform.audit.global.view",
    "platform.access.view",
    "platform.access.manage",
    "platform.accounts.view",
    "platform.accounts.manage",
}


async def _template_codes(db_session: AsyncSession, template_name: str) -> list[str]:
    stmt = (
        select(RoleTemplatePermission.permission_code)
        .join(RoleTemplate, RoleTemplate.id == RoleTemplatePermission.template_id)
        .where(RoleTemplate.name == template_name)
    )
    return list((await db_session.execute(stmt)).scalars().all())


async def test_seed_permissions_count(db_session: AsyncSession) -> None:
    """The migrated permission catalogue remains complete and duplicate-free."""
    count = (await db_session.execute(select(func.count()).select_from(Permission))).scalar_one()
    assert count == 69

    groups = (
        await db_session.execute(select(func.count(func.distinct(Permission.group_code))))
    ).scalar_one()
    assert groups == 21


async def test_every_permission_has_a_description(db_session: AsyncSession) -> None:
    """Migration 0021 backfilled a human-readable tooltip for every permission
    (used by the role builder); none should be left NULL/blank."""
    rows = (await db_session.execute(select(Permission.code, Permission.description))).all()
    missing = [code for code, description in rows if not (description or "").strip()]
    assert not missing, f"permissions missing a description: {missing}"


async def test_seed_system_roles_exist(db_session: AsyncSession) -> None:
    """Only developer / administrator remain system roles — owner / seller were
    demoted to tenant roles (migration 0020) and live on as templates."""
    stmt = select(Role).where(Role.is_system.is_(True))
    result = await db_session.execute(stmt)
    by_name = {r.name: r for r in result.scalars().all()}

    expected_levels = {
        "developer": 1,
        "administrator": 2,
    }
    assert set(by_name.keys()) == set(expected_levels.keys())
    for name, level in expected_levels.items():
        assert by_name[name].level == level
        assert by_name[name].tenant_id is None
    assert "owner" not in by_name
    assert "seller" not in by_name


async def test_kassir_template_has_only_min_level_4_permissions(
    db_session: AsyncSession,
) -> None:
    """The «Кассир» preset (the former seller set) must hold only
    cashier-level (min_level_required = 4) permissions."""
    codes = await _template_codes(db_session, "Кассир")
    assert codes, "the Кассир template should have at least one permission"
    perms = (
        await db_session.execute(
            select(Permission.code, Permission.min_level_required).where(Permission.code.in_(codes))
        )
    ).all()
    for _, mlr in perms:
        assert mlr == 4, f"Кассир template has perm with min_level_required={mlr}"
    assert "pos.refund_external_confirm" not in codes


async def test_vladelec_template_excludes_global_audit(db_session: AsyncSession) -> None:
    """The «Владелец» preset (the former owner set) keeps owner reach but never
    the developer-only cross-tenant audit."""
    codes = set(await _template_codes(db_session, "Владелец"))
    assert "audit.view.global" not in codes
    assert "users.invite" in codes
    assert "pos.sell" in codes
    assert "pos.refund_external_confirm" in codes


async def test_developer_has_everything(db_session: AsyncSession, system_roles) -> None:
    dev = system_roles["developer"]
    dev_codes = set(
        (
            await db_session.execute(
                select(RolePermission.permission_code).where(RolePermission.role_id == dev.id)
            )
        )
        .scalars()
        .all()
    )
    all_codes = set((await db_session.execute(select(Permission.code))).scalars().all())
    # Platform capabilities are attached to immutable access grants, never to
    # tenant/system roles. The legacy global-audit permission remains on the
    # developer role until its separate compatibility migration is removed.
    assert dev_codes == all_codes - GRANT_SCOPED_PLATFORM_CODES
    assert dev_codes.isdisjoint(GRANT_SCOPED_PLATFORM_CODES)
