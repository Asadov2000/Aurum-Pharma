"""Delegation-envelope and protected-role tests for the tenant role builder."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.errors import PermissionDeniedError, ValidationError
from app.domains.audit.models import AuditLog
from app.domains.roles.models import Permission, Role, RolePermission
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import CUSTOM_ROLE_LEGACY_LEVEL, RolesService
from app.main import app
from tests.auth_helpers import create_tenant_access_token
from tests.role_version_helpers import set_test_recent_confirmation

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
    assert global_error.value.details == {
        "reason": "delegation_envelope_exceeded",
        "permissions": ["audit.view.global"],
    }

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
    assert governance_error.value.details == {
        "reason": "delegation_envelope_exceeded",
        "permissions": ["roles.assign"],
    }

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


async def test_tenant_role_never_receives_platform_scope_from_malformed_catalog(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repo = RolesRepository(db_session)
    service = RolesService(repo)
    permission = await db_session.get(Permission, "pos.sell")
    assert permission is not None
    permission.scope_type = "PLATFORM"
    permission.target_role_type = "tenant"
    await db_session.flush()

    owner_permissions = await _owner_permissions(repo, owner_role)
    catalog = await service.list_permissions(
        actor_id=owner.id,
        tenant_id=tenant.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
    )
    assert "pos.sell" not in {item.code for item in catalog}

    with pytest.raises(PermissionDeniedError) as error:
        await service.create_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            name="Некорректная платформенная роль",
            description=None,
            permission_codes=["pos.sell"],
        )
    assert error.value.details == {
        "reason": "delegation_envelope_exceeded",
        "permissions": ["pos.sell"],
    }


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    custom_role = Role(
        tenant_id=tenant.id,
        name="Self-managed role",
        level=CUSTOM_ROLE_LEGACY_LEVEL,
    )
    db_session.add(custom_role)
    await db_session.flush()
    await db_session.refresh(custom_role)
    repo = RolesRepository(db_session)
    monkeypatch.setattr(
        repo,
        "user_has_active_role",
        AsyncMock(return_value=True),
    )

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


async def test_hidden_capability_cannot_be_injected_into_published_role(
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
    savepoint = await db_session.begin_nested()
    with pytest.raises(DBAPIError, match="publication workflow"):
        db_session.add(
            RolePermission(
                role_id=role.id,
                permission_code="audit.view.global",
            )
        )
        await db_session.flush()
    await savepoint.rollback()

    assert role.description is None
    assert await repo.get_role_permissions(role.id) == ["pos.sell"]


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
        token = await create_tenant_access_token(
            db_session,
            owner,
            tenant_id=tenant.id,
            mfa_verified_at=datetime.now(UTC),
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


async def test_readonly_tenant_cannot_create_role(
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
        token = await create_tenant_access_token(
            db_session,
            owner,
            tenant_id=tenant.id,
            mfa_verified_at=datetime.now(UTC),
        )
        tenant.status = "readonly"
        await db_session.flush()

        response = await client.post(
            "/api/v1/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Недоступная роль", "permissions": ["pos.sell"]},
        )

        assert response.status_code == 422
        assert response.json()["error"] == {
            "code": "business_rule_violation",
            "message": "Аптека работает в режиме только чтения. Изменения недоступны.",
            "details": {"status": "readonly"},
        }
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_role_update_rejects_stale_expected_version_with_409(
    db_session: AsyncSession,
    client: AsyncClient,
    make_tenant,
    make_owner,
    make_user,
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
        assigned_user = await make_user(home_tenant_id=tenant.id)
        await repo.insert_assignment(
            user_id=assigned_user.id,
            tenant_id=tenant.id,
            branch_id=None,
            role_id=role.id,
            password_required=False,
        )
        token = await create_tenant_access_token(
            db_session,
            owner,
            tenant_id=tenant.id,
            mfa_verified_at=datetime.now(UTC),
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
        assert first.json()["active_assignment_count"] == 1
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
        is_developer=True,
    )
    await set_test_recent_confirmation(db_session, user_id=developer.id)
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(developer.id)},
    )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant.id)},
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
        permission_codes=["catalog.view"],
    )
    assert role.tenant_id == tenant.id
    assert codes == ["catalog.view"]

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
