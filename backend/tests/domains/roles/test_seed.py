"""Migration seed: permissions catalogue and system roles."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.roles.models import Permission, Role, RolePermission


async def test_seed_permissions_count(db_session: AsyncSession) -> None:
    """43 distinct permissions across 14 groups (41 base in 13 groups + the
    sales.view.own / sales.view.tenant 'sales' group from migration 0014)."""
    count = (await db_session.execute(select(func.count()).select_from(Permission))).scalar_one()
    assert count == 43

    groups = (
        await db_session.execute(select(func.count(func.distinct(Permission.group_code))))
    ).scalar_one()
    assert groups == 14  # +'sales' group from migration 0014


async def test_seed_system_roles_exist(db_session: AsyncSession) -> None:
    stmt = select(Role).where(Role.is_system.is_(True))
    result = await db_session.execute(stmt)
    by_name = {r.name: r for r in result.scalars().all()}

    expected_levels = {
        "developer": 1,
        "administrator": 2,
        "owner": 3,
        "seller": 4,
    }
    assert set(by_name.keys()) == set(expected_levels.keys())
    for name, level in expected_levels.items():
        assert by_name[name].level == level
        assert by_name[name].tenant_id is None


async def test_seller_has_only_min_level_4_permissions(
    db_session: AsyncSession, system_roles
) -> None:
    seller = system_roles["seller"]
    codes = (
        (
            await db_session.execute(
                select(RolePermission.permission_code).where(RolePermission.role_id == seller.id)
            )
        )
        .scalars()
        .all()
    )
    perms = (
        await db_session.execute(
            select(Permission.code, Permission.min_level_required).where(Permission.code.in_(codes))
        )
    ).all()
    assert codes, "seller should have at least one permission"
    for _, mlr in perms:
        assert mlr == 4, f"seller has perm with min_level_required={mlr}"


async def test_owner_excludes_global_audit(db_session: AsyncSession, system_roles) -> None:
    owner = system_roles["owner"]
    codes = set(
        (
            await db_session.execute(
                select(RolePermission.permission_code).where(RolePermission.role_id == owner.id)
            )
        )
        .scalars()
        .all()
    )
    assert "audit.view.global" not in codes
    assert "users.invite" in codes
    assert "pos.sell" in codes  # owner also inherits seller-level perms


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
    assert dev_codes == all_codes
