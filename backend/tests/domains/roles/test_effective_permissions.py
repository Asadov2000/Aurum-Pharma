"""Effective authorization state must fail closed."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.roles.models import Permission, TenantMembership, UserAssignment
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService


async def _assign(
    db_session: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID,
    role_id: UUID,
) -> UserAssignment:
    assignment = UserAssignment(
        user_id=user_id,
        tenant_id=tenant_id,
        role_id=role_id,
    )
    db_session.add(assignment)
    await db_session.flush()
    return assignment


async def test_effective_permissions_follow_archived_role_replacement(
    db_session: AsyncSession, make_owner, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    user = await make_user(home_tenant_id=tenant.id)
    repository = RolesRepository(db_session)
    service = RolesService(repository)
    owner_permissions = set(await repository.get_role_permissions(owner_role.id))
    role, _permissions = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Role to archive",
        description=None,
        permission_codes=["pos.sell"],
    )
    replacement, _permissions = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Archive replacement",
        description=None,
        permission_codes=["catalog.view"],
    )
    await repository.insert_assignment(
        user_id=user.id,
        tenant_id=tenant.id,
        branch_id=None,
        role_id=role.id,
        password_required=False,
    )

    assert "pos.sell" in await repository.effective_permissions(user.id, tenant.id)

    await service.archive_role_with_replacement(
        actor_id=owner.id,
        tenant_id=tenant.id,
        role_id=role.id,
        expected_version=role.version,
        replacement_role_id=replacement.id,
    )

    permissions = await repository.effective_permissions(user.id, tenant.id)
    assert "pos.sell" not in permissions
    assert "catalog.view" in permissions


async def test_effective_permissions_exclude_inactive_permission(
    db_session: AsyncSession, make_tenant, make_tenant_role, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    role = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
    )
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=role.id,
    )
    permission = await db_session.get(Permission, "pos.sell")
    assert permission is not None
    permission.is_active = False
    await db_session.flush()

    permissions = await RolesRepository(db_session).effective_permissions(user.id, tenant.id)

    assert "pos.sell" not in permissions
    assert "catalog.view" in permissions


async def test_effective_permissions_exclude_inactive_assignment(
    db_session: AsyncSession, make_tenant, make_tenant_role, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    role = await make_tenant_role(
        tenant_id=tenant.id,
        template_name="Кассир",
        level=4,
    )
    assignment = await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant.id,
        role_id=role.id,
    )
    assignment.is_active = False
    await db_session.flush()

    assert await RolesRepository(db_session).effective_permissions(user.id, tenant.id) == set()


async def test_effective_permissions_are_isolated_by_tenant(
    db_session: AsyncSession, make_tenant, make_tenant_role, make_user
) -> None:
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()
    user = await make_user(home_tenant_id=tenant_a.id)
    role_a = await make_tenant_role(
        tenant_id=tenant_a.id,
        template_name="Кассир",
        level=4,
        name="Tenant A role",
    )
    role_b = await make_tenant_role(
        tenant_id=tenant_b.id,
        template_name="Владелец",
        level=3,
        name="Tenant B role",
    )
    db_session.add(
        TenantMembership(
            tenant_id=tenant_b.id,
            user_id=user.id,
            full_name=user.full_name,
            status="active",
        )
    )
    await db_session.flush()
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant_a.id,
        role_id=role_a.id,
    )
    await _assign(
        db_session,
        user_id=user.id,
        tenant_id=tenant_b.id,
        role_id=role_b.id,
    )
    repository = RolesRepository(db_session)

    permissions_a = await repository.effective_permissions(user.id, tenant_a.id)
    permissions_b = await repository.effective_permissions(user.id, tenant_b.id)

    assert "pos.sell" in permissions_a
    assert "users.invite" not in permissions_a
    assert "users.invite" in permissions_b
