"""Custom-role builder: create_role / update_role with anti-escalation,
permission-subset and system-role protections."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.errors import ConflictError, PermissionDeniedError, ValidationError
from app.core.security import create_access_token
from app.domains.roles.models import UserAssignment
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from app.main import app


async def test_owner_creates_tenant_role_from_subset(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-rb@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    role, codes = await service.create_role(
        actor_level=3,  # owner
        actor_id=actor.id,
        actor_permissions={"pos.sell", "catalog.view"},
        actor_is_support=False,
        tenant_id=tenant.id,
        name="Стажёр",
        description="Помощник кассира",
        level=4,  # strictly weaker than owner (3)
        permission_codes=["pos.sell"],
    )

    assert role.tenant_id == tenant.id
    assert role.is_system is False
    assert role.level == 4
    assert role.name == "Стажёр"
    assert codes == ["pos.sell"]
    assert await service.repo.get_role_permissions(role.id) == ["pos.sell"]


async def test_create_role_at_or_above_own_level_denied(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-esc@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    # Equal level (owner -> owner) is refused — not strictly weaker.
    with pytest.raises(PermissionDeniedError):
        await service.create_role(
            actor_level=3,
            actor_id=actor.id,
            actor_permissions={"pos.sell"},
            actor_is_support=False,
            tenant_id=tenant.id,
            name="Равный",
            description=None,
            level=3,
            permission_codes=[],
        )

    # Stronger level (owner -> administrator) is refused too.
    with pytest.raises(PermissionDeniedError):
        await service.create_role(
            actor_level=3,
            actor_id=actor.id,
            actor_permissions={"pos.sell"},
            actor_is_support=False,
            tenant_id=tenant.id,
            name="Сильнее",
            description=None,
            level=2,
            permission_codes=[],
        )


async def test_create_role_with_foreign_permission_denied(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-foreign@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    # Actor holds only pos.sell but tries to mint a role with catalog.delete.
    with pytest.raises(PermissionDeniedError):
        await service.create_role(
            actor_level=3,
            actor_id=actor.id,
            actor_permissions={"pos.sell"},
            actor_is_support=False,
            tenant_id=tenant.id,
            name="Слишком много",
            description=None,
            level=4,
            permission_codes=["catalog.delete"],
        )


async def test_create_role_permission_must_fit_target_level(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-level-perm@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    with pytest.raises(PermissionDeniedError):
        await service.create_role(
            actor_level=3,
            actor_id=actor.id,
            actor_permissions={"pos.sell", "users.invite"},
            actor_is_support=False,
            tenant_id=tenant.id,
            name="Кассир с правами владельца",
            description=None,
            level=4,
            permission_codes=["pos.sell", "users.invite"],
        )


async def test_create_role_unknown_permission_rejected(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-unknown@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    with pytest.raises(ValidationError):
        await service.create_role(
            actor_level=3,
            actor_id=actor.id,
            actor_permissions={"pos.sell"},
            actor_is_support=False,
            tenant_id=tenant.id,
            name="Битый код",
            description=None,
            level=4,
            permission_codes=["totally.bogus"],
        )


async def test_duplicate_role_name_conflicts(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-dup@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    await service.create_role(
        actor_level=3,
        actor_id=actor.id,
        actor_permissions={"pos.sell"},
        actor_is_support=False,
        tenant_id=tenant.id,
        name="Кассир-стажёр",
        description=None,
        level=4,
        permission_codes=["pos.sell"],
    )
    with pytest.raises(ConflictError):
        await service.create_role(
            actor_level=3,
            actor_id=actor.id,
            actor_permissions={"pos.sell"},
            actor_is_support=False,
            tenant_id=tenant.id,
            name="Кассир-стажёр",  # same name in the same tenant
            description=None,
            level=4,
            permission_codes=["pos.sell"],
        )


async def test_update_system_role_forbidden(
    db_session: AsyncSession, make_tenant, system_roles, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="dev-sys@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    # Even a developer (level 1, support) cannot edit a seeded system role here
    # (administrator remains a system role after the owner/seller demotion).
    with pytest.raises(PermissionDeniedError):
        await service.update_role(
            actor_level=1,
            actor_id=actor.id,
            actor_permissions=set(),
            actor_is_support=True,
            tenant_id=tenant.id,
            role_id=system_roles["administrator"].id,
            name="Взлом",
            description=None,
            level=None,
            permission_codes=None,
        )


async def test_update_tenant_role_changes_name_and_permissions(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-upd@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))
    perms = {"pos.sell", "catalog.view", "catalog.update"}

    role, _ = await service.create_role(
        actor_level=3,
        actor_id=actor.id,
        actor_permissions=perms,
        actor_is_support=False,
        tenant_id=tenant.id,
        name="Стажёр",
        description=None,
        level=4,
        permission_codes=["pos.sell"],
    )

    updated, codes = await service.update_role(
        actor_level=3,
        actor_id=actor.id,
        actor_permissions=perms,
        actor_is_support=False,
        tenant_id=tenant.id,
        role_id=role.id,
        name="Старший кассир",
        description="Обновлено",
        level=None,
        permission_codes=["pos.sell", "catalog.view"],
    )

    assert updated.id == role.id
    assert updated.name == "Старший кассир"
    assert updated.description == "Обновлено"
    assert sorted(codes) == ["catalog.view", "pos.sell"]


async def test_update_role_permission_must_fit_target_level(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="owner-upd-level@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))
    perms = {"pos.sell", "users.invite"}

    role, _ = await service.create_role(
        actor_level=3,
        actor_id=actor.id,
        actor_permissions=perms,
        actor_is_support=False,
        tenant_id=tenant.id,
        name="Кассир",
        description=None,
        level=4,
        permission_codes=["pos.sell"],
    )

    with pytest.raises(PermissionDeniedError):
        await service.update_role(
            actor_level=3,
            actor_id=actor.id,
            actor_permissions=perms,
            actor_is_support=False,
            tenant_id=tenant.id,
            role_id=role.id,
            name=None,
            description=None,
            level=None,
            permission_codes=["pos.sell", "users.invite"],
        )


async def test_update_role_level_requires_existing_permissions_to_fit(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="dev-lower-level@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    role, _ = await service.create_role(
        actor_level=1,
        actor_id=actor.id,
        actor_permissions=set(),
        actor_is_support=True,
        tenant_id=tenant.id,
        name="Менеджер",
        description=None,
        level=3,
        permission_codes=["users.invite"],
    )

    with pytest.raises(PermissionDeniedError):
        await service.update_role(
            actor_level=1,
            actor_id=actor.id,
            actor_permissions=set(),
            actor_is_support=True,
            tenant_id=tenant.id,
            role_id=role.id,
            name=None,
            description=None,
            level=4,
            permission_codes=None,
        )


async def test_role_create_endpoint_enforces_permission_level(
    db_session: AsyncSession,
    client: AsyncClient,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await make_tenant()
        owner = await make_user(email="owner-http-rb@aurum.tj", home_tenant_id=tenant.id)
        owner_role = await make_tenant_role(tenant_id=tenant.id, template_name="Владелец", level=3)
        db_session.add(
            UserAssignment(
                user_id=owner.id,
                tenant_id=tenant.id,
                role_id=owner_role.id,
                is_active=True,
            )
        )
        await db_session.flush()

        token = create_access_token(
            owner.id,
            tenant_id=tenant.id,
            is_developer=False,
            is_administrator=False,
        )
        resp = await client.post(
            "/api/v1/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Кассир с invite",
                "level": 4,
                "permissions": ["pos.sell", "users.invite"],
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["details"]["permissions"] == ["users.invite"]
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_support_bypasses_subset_and_creates_below_self(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="dev-build@aurum.tj", home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    # Developer (level 1, support) holds no resolved permissions but may still
    # grant any existing code, and may create a level-2 role (weaker than 1).
    role, codes = await service.create_role(
        actor_level=1,
        actor_id=actor.id,
        actor_permissions=set(),
        actor_is_support=True,
        tenant_id=tenant.id,
        name="Менеджер сети",
        description=None,
        level=2,
        permission_codes=["catalog.delete"],
    )

    assert role.level == 2
    assert codes == ["catalog.delete"]
