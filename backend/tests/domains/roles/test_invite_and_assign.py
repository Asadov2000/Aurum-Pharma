"""invite_user, assign_role, anti-escalation."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService


async def test_invite_creates_user_and_assignment(
    db_session: AsyncSession, make_tenant, system_roles, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="actor1@aurum.tj")
    service = RolesService(RolesRepository(db_session))

    user, assignment, first_invite = await service.invite_user(
        actor_level=2,  # administrator
        actor_id=actor.id,
        tenant_id=tenant.id,
        email="newhire@aurum.tj",
        full_name="New Hire",
        role_id=system_roles["seller"].id,
        branch_id=None,
        password_required=False,
    )
    assert first_invite is True
    assert user.email_lower == "newhire@aurum.tj"
    assert user.status == "invited"
    assert assignment.user_id == user.id
    assert assignment.tenant_id == tenant.id
    assert assignment.role_id == system_roles["seller"].id


async def test_invite_existing_user_creates_assignment_only(
    db_session: AsyncSession, make_tenant, system_roles, make_user
) -> None:
    tenant = await make_tenant()
    existing = await make_user(email="existing@aurum.tj")
    service = RolesService(RolesRepository(db_session))

    user, assignment, first_invite = await service.invite_user(
        actor_level=2,
        actor_id=existing.id,
        tenant_id=tenant.id,
        email="existing@aurum.tj",
        full_name="Existing User",
        role_id=system_roles["seller"].id,
        branch_id=None,
        password_required=False,
    )
    assert first_invite is False
    assert user.id == existing.id
    assert assignment.user_id == existing.id


async def test_anti_escalation_owner_cannot_grant_administrator(
    db_session: AsyncSession, make_tenant, system_roles, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-actor@aurum.tj")
    service = RolesService(RolesRepository(db_session))

    with pytest.raises(BusinessRuleError):
        await service.invite_user(
            actor_level=3,  # owner
            actor_id=actor.id,
            tenant_id=tenant.id,
            email="evilboss@aurum.tj",
            full_name="Evil Boss",
            role_id=system_roles["administrator"].id,  # level 2 — higher
            branch_id=None,
            password_required=False,
        )


async def test_anti_escalation_administrator_cannot_grant_developer(
    db_session: AsyncSession, make_tenant, system_roles, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="admin-actor@aurum.tj")
    service = RolesService(RolesRepository(db_session))

    with pytest.raises(BusinessRuleError):
        await service.invite_user(
            actor_level=2,
            actor_id=actor.id,
            tenant_id=tenant.id,
            email="evildev@aurum.tj",
            full_name="Evil Dev",
            role_id=system_roles["developer"].id,
            branch_id=None,
            password_required=False,
        )


async def test_developer_can_grant_anything(
    db_session: AsyncSession, make_tenant, system_roles, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="dev-actor@aurum.tj")
    service = RolesService(RolesRepository(db_session))

    _, assignment, _ = await service.invite_user(
        actor_level=1,
        actor_id=actor.id,
        tenant_id=tenant.id,
        email="seconddev@aurum.tj",
        full_name="Second Dev",
        role_id=system_roles["developer"].id,
        branch_id=None,
        password_required=False,
    )
    assert assignment.role_id == system_roles["developer"].id


async def test_duplicate_assignment_returns_conflict(
    db_session: AsyncSession, make_tenant, system_roles, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="dup-actor@aurum.tj")
    service = RolesService(RolesRepository(db_session))

    await service.invite_user(
        actor_level=2,
        actor_id=actor.id,
        tenant_id=tenant.id,
        email="dup@aurum.tj",
        full_name="Dup",
        role_id=system_roles["seller"].id,
        branch_id=None,
        password_required=False,
    )
    with pytest.raises(ConflictError):
        await service.invite_user(
            actor_level=2,
            actor_id=actor.id,
            tenant_id=tenant.id,
            email="dup@aurum.tj",
            full_name="Dup",
            role_id=system_roles["seller"].id,
            branch_id=None,
            password_required=False,
        )


async def test_effective_permissions_match_role_set(
    db_session: AsyncSession, make_tenant, system_roles, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="eff-actor@aurum.tj")
    service = RolesService(RolesRepository(db_session))

    user, _, _ = await service.invite_user(
        actor_level=2,
        actor_id=actor.id,
        tenant_id=tenant.id,
        email="effective@aurum.tj",
        full_name="E",
        role_id=system_roles["seller"].id,
        branch_id=None,
        password_required=False,
    )

    perms = await service.repo.effective_permissions(user.id, tenant.id)
    assert "pos.sell" in perms
    assert "catalog.view" in perms
    assert "users.invite" not in perms  # seller cannot invite
