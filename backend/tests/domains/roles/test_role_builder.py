"""Delegation-envelope and protected-role tests for the tenant role builder."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.errors import PermissionDeniedError, ValidationError
from app.core.security import create_access_token
from app.domains.audit.models import AuditLog
from app.domains.roles.models import Role, RolePermission
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import CUSTOM_ROLE_LEGACY_LEVEL, RolesService
from app.main import app

PROTECTED_GOVERNANCE_CODES = {
    "roles.assign",
    "roles.create",
    "roles.update",
    "users.block",
    "users.delete",
    "users.invite",
    "users.update",
}


async def _owner_permissions(repo: RolesRepository, owner_role: Role) -> set[str]:
    return set(await repo.get_role_permissions(owner_role.id))


async def test_owner_creates_role_from_strict_delegable_subset_and_audits_diff(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repo = RolesRepository(db_session)
    service = RolesService(repo)

    role, codes = await service.create_role(
        actor_id=owner.id,
        actor_permissions=await _owner_permissions(repo, owner_role),
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Стажёр",
        description="Помощник кассира",
        permission_codes=["pos.sell", "catalog.view"],
    )

    assert role.level == CUSTOM_ROLE_LEGACY_LEVEL
    assert role.version == 1
    assert role.is_protected is False
    assert codes == ["catalog.view", "pos.sell"]

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "ROLE_PERMISSIONS_CHANGED",
                AuditLog.record_id == role.id,
            )
        )
    ).scalar_one()
    assert audit.metadata_json is not None
    assert audit.metadata_json["before_permissions"] == []
    assert audit.metadata_json["after_permissions"] == codes
    assert "email" not in audit.metadata_json


async def test_owner_can_use_full_visible_catalog_without_protected_governance(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repo = RolesRepository(db_session)
    service = RolesService(repo)
    owner_permissions = await _owner_permissions(repo, owner_role)
    catalog = await service.list_permissions(
        actor_id=owner.id,
        tenant_id=tenant.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
    )

    visible_codes = {permission.code for permission in catalog}
    role, codes = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Полный бизнес-доступ",
        description=None,
        permission_codes=sorted(visible_codes),
    )

    assert role.is_protected is False
    assert set(codes) == visible_codes
    assert visible_codes.isdisjoint(PROTECTED_GOVERNANCE_CODES)


async def test_owner_cannot_grant_global_protected_or_unknown_permission(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repo = RolesRepository(db_session)
    service = RolesService(repo)
    owner_permissions = await _owner_permissions(repo, owner_role)

    with pytest.raises(PermissionDeniedError) as global_error:
        await service.create_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            name="Глобальный аудит",
            description=None,
            permission_codes=["audit.view.global"],
        )
    assert global_error.value.details == {"permissions": ["audit.view.global"]}

    with pytest.raises(PermissionDeniedError) as governance_error:
        await service.create_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            name="Управление ролями",
            description=None,
            permission_codes=["roles.assign"],
        )
    assert governance_error.value.details == {"permissions": ["roles.assign"]}

    with pytest.raises(ValidationError) as unknown_error:
        await service.create_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            name="Неизвестное право",
            description=None,
            permission_codes=["missing.permission"],
        )
    assert unknown_error.value.details == {"permissions": ["missing.permission"]}


async def test_protected_owner_role_cannot_be_edited(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repo = RolesRepository(db_session)

    with pytest.raises(PermissionDeniedError, match="Protected"):
        await RolesService(repo).update_role(
            actor_id=owner.id,
            actor_permissions=await _owner_permissions(repo, owner_role),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            role_id=owner_role.id,
            expected_version=owner_role.version,
            name="Новый владелец",
            description=None,
            permission_codes=None,
        )


async def test_actor_cannot_edit_role_assigned_to_self(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    custom_role = Role(
        tenant_id=tenant.id,
        name="Дополнительная роль владельца",
        level=CUSTOM_ROLE_LEGACY_LEVEL,
    )
    db_session.add(custom_role)
    await db_session.flush()
    await db_session.refresh(custom_role)
    owner_assignment = (
        await RolesRepository(db_session).list_assignments_for_user(
            owner.id,
            tenant_id=tenant.id,
        )
    )[0]
    owner_assignment.role_id = custom_role.id
    await db_session.flush()
    repo = RolesRepository(db_session)

    with pytest.raises(PermissionDeniedError, match="own active role"):
        await RolesService(repo).update_role(
            actor_id=owner.id,
            actor_permissions=await _owner_permissions(repo, owner_role),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            role_id=custom_role.id,
            expected_version=custom_role.version,
            name=None,
            description="Changed",
            permission_codes=[],
        )


async def test_role_update_increments_version_and_records_before_after(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repo = RolesRepository(db_session)
    service = RolesService(repo)
    owner_permissions = await _owner_permissions(repo, owner_role)
    role, _ = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Кассир-стажёр",
        description=None,
        permission_codes=["pos.sell"],
    )

    updated, codes = await service.update_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        role_id=role.id,
        expected_version=role.version,
        name=None,
        description=None,
        permission_codes=["catalog.view"],
    )

    assert updated.version == 2
    assert codes == ["catalog.view"]
    events = list(
        (
            await db_session.execute(
                select(AuditLog)
                .where(
                    AuditLog.action == "ROLE_PERMISSIONS_CHANGED",
                    AuditLog.record_id == role.id,
                )
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    update_event = next(
        event
        for event in events
        if event.metadata_json is not None and event.metadata_json.get("role_version") == 2
    )
    assert update_event.metadata_json == {
        "role_id": str(role.id),
        "role_version": 2,
        "before_permissions": ["pos.sell"],
        "after_permissions": ["catalog.view"],
    }


async def test_role_with_hidden_capability_cannot_be_modified_by_forged_request(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repo = RolesRepository(db_session)
    service = RolesService(repo)
    owner_permissions = await _owner_permissions(repo, owner_role)
    role, _codes = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Legacy protected mix",
        description=None,
        permission_codes=["pos.sell"],
    )
    db_session.add(
        RolePermission(
            role_id=role.id,
            permission_code="audit.view.global",
        )
    )
    await db_session.flush()

    with pytest.raises(
        PermissionDeniedError,
        match="outside the delegation envelope",
    ):
        await service.update_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            role_id=role.id,
            expected_version=role.version,
            name=None,
            description="Forged update",
            permission_codes=["pos.sell"],
        )

    assert role.description is None


async def test_public_role_contract_rejects_numeric_level(
    db_session: AsyncSession,
    client: AsyncClient,
    make_tenant,
    make_owner,
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await make_tenant()
        owner, _membership, _ownership, _owner_role = await make_owner(tenant_id=tenant.id)
        token = create_access_token(
            owner.id,
            tenant_id=tenant.id,
            is_developer=False,
            is_administrator=False,
        )

        response = await client.post(
            "/api/v1/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Legacy level",
                "level": 4,
                "permissions": ["pos.sell"],
            },
        )

        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_role_update_rejects_stale_expected_version_with_409(
    db_session: AsyncSession,
    client: AsyncClient,
    make_tenant,
    make_owner,
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await make_tenant()
        owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
        repo = RolesRepository(db_session)
        role, _codes = await RolesService(repo).create_role(
            actor_id=owner.id,
            actor_permissions=await _owner_permissions(repo, owner_role),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            name="Optimistic role",
            description=None,
            permission_codes=["pos.sell"],
        )
        token = create_access_token(
            owner.id,
            tenant_id=tenant.id,
            is_developer=False,
            is_administrator=False,
        )
        headers = {"Authorization": f"Bearer {token}"}
        initial_version = role.version

        first = await client.patch(
            f"/api/v1/roles/{role.id}",
            headers=headers,
            json={"expected_version": initial_version, "description": "first"},
        )
        stale = await client.patch(
            f"/api/v1/roles/{role.id}",
            headers=headers,
            json={"expected_version": initial_version, "description": "stale"},
        )

        assert first.status_code == 200
        assert first.json()["version"] == 2
        assert stale.status_code == 409
        assert stale.json()["error"] == {
            "code": "conflict",
            "message": "Role version is stale",
            "details": {
                "expected_version": 1,
                "current_version": 2,
            },
        }
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_developer_can_create_tenant_role_but_not_platform_grant(
    db_session: AsyncSession,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    developer = await make_user(
        email="developer-builder@aurum.tj",
        home_tenant_id=tenant.id,
    )
    service = RolesService(RolesRepository(db_session))

    role, codes = await service.create_role(
        actor_id=developer.id,
        actor_permissions=set(),
        actor_is_developer=True,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Менеджер",
        description=None,
        permission_codes=["tenant.export.full"],
    )
    assert role.tenant_id == tenant.id
    assert codes == ["tenant.export.full"]

    with pytest.raises(PermissionDeniedError):
        await service.create_role(
            actor_id=developer.id,
            actor_permissions=set(),
            actor_is_developer=True,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            name="Platform role",
            description=None,
            permission_codes=["audit.view.global"],
        )
