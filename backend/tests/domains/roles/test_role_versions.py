"""Immutable role publication is the only path that may change active access."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, PermissionDeniedError
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.roles.models import (
    AccessRoleVersion,
    Role,
    TenantMembership,
    TenantOwnership,
)
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from tests.role_version_helpers import create_published_test_role, set_test_recent_confirmation


class TenantFactory(Protocol):
    async def __call__(self, name: str | None = None) -> Tenant: ...


class OwnerFactory(Protocol):
    async def __call__(
        self,
        *,
        tenant_id: UUID,
        email: str | None = None,
        full_name: str = "Owner",
    ) -> tuple[AppUser, TenantMembership, TenantOwnership, Role]: ...


class UserFactory(Protocol):
    async def __call__(
        self,
        *,
        email: str | None = None,
        full_name: str = "Test User",
        home_tenant_id: UUID | None = None,
        status: str = "active",
        membership_status: str = "active",
        is_owner: bool = False,
        is_developer: bool = False,
        is_administrator: bool = False,
    ) -> AppUser: ...


async def _owner_permissions(repository: RolesRepository, role_id: UUID) -> set[str]:
    return set(await repository.get_role_permissions(role_id))


async def test_publication_preserves_history_and_repoints_active_assignment(
    db_session: AsyncSession,
    make_owner: OwnerFactory,
    make_tenant: TenantFactory,
    make_user: UserFactory,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    target = await make_user(home_tenant_id=tenant.id)
    repository = RolesRepository(db_session)
    service = RolesService(repository)
    owner_permissions = await _owner_permissions(repository, owner_role.id)
    role, initial_permissions = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Кассир пилота",
        description="Первая версия",
        permission_codes=["catalog.view"],
    )
    assignment = await repository.insert_assignment(
        user_id=target.id,
        tenant_id=tenant.id,
        branch_id=None,
        role_id=role.id,
        password_required=False,
    )
    initial_version_id = assignment.role_version_id

    updated, published_permissions = await service.update_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        role_id=role.id,
        expected_version=role.version,
        name="Кассир пилота",
        description="Продажа разрешена",
        permission_codes=["catalog.view", "pos.sell"],
    )

    await db_session.refresh(assignment)
    versions = await repository.list_role_versions(role.id)
    snapshot = await repository.authorization_snapshot(target.id, tenant.id)
    assert initial_permissions == ["catalog.view"]
    assert published_permissions == ["catalog.view", "pos.sell"]
    assert updated.version == 2
    assert assignment.role_version_id != initial_version_id
    assert [(version.version, version.status) for version in versions] == [
        (2, "published"),
        (1, "archived"),
    ]
    assert versions[0].permissions == ("catalog.view", "pos.sell")
    assert versions[1].permissions == ("catalog.view",)
    assert snapshot.permissions.issuperset({"catalog.view", "pos.sell"})


async def test_direct_role_and_published_version_mutation_is_denied(
    db_session: AsyncSession,
    make_owner: OwnerFactory,
    make_tenant: TenantFactory,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    repository = RolesRepository(db_session)
    role, _permissions = await RolesService(repository).create_role(
        actor_id=owner.id,
        actor_permissions=await _owner_permissions(repository, owner_role.id),
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Неизменяемая роль",
        description=None,
        permission_codes=["catalog.view"],
    )
    version_id = await repository.get_published_role_version_id(role.id)
    assert version_id is not None

    role_savepoint = await db_session.begin_nested()
    try:
        role_result = await db_session.execute(
            text("UPDATE public.role SET name = 'Обход' " "WHERE id = :role_id RETURNING id"),
            {"role_id": role.id},
        )
        role_denied = role_result.scalar_one_or_none() is None
    except DBAPIError:
        role_denied = True
    finally:
        await role_savepoint.rollback()
    assert role_denied

    version_savepoint = await db_session.begin_nested()
    try:
        version_result = await db_session.execute(
            text(
                "UPDATE public.access_role_version SET name = 'Обход' "
                "WHERE id = :id RETURNING id"
            ),
            {"id": version_id},
        )
        version_denied = version_result.scalar_one_or_none() is None
    except DBAPIError:
        version_denied = True
    finally:
        await version_savepoint.rollback()
    assert version_denied

    unchanged = await db_session.get(AccessRoleVersion, version_id)
    assert unchanged is not None
    assert unchanged.name == "Неизменяемая роль"


async def test_stale_publication_is_rejected(
    db_session: AsyncSession,
    make_owner: OwnerFactory,
    make_tenant: TenantFactory,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repository = RolesRepository(db_session)
    service = RolesService(repository)
    owner_permissions = await _owner_permissions(repository, owner_role.id)
    role, _permissions = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Версионная роль",
        description=None,
        permission_codes=["catalog.view"],
    )
    await service.update_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        role_id=role.id,
        expected_version=1,
        name="Версионная роль 2",
        description=None,
        permission_codes=["catalog.view"],
    )

    with pytest.raises(ConflictError, match="stale"):
        await service.update_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            role_id=role.id,
            expected_version=1,
            name="Устаревшая запись",
            description=None,
            permission_codes=["catalog.view"],
        )


async def test_archive_atomically_replaces_active_assignments(
    db_session: AsyncSession,
    make_owner: OwnerFactory,
    make_tenant: TenantFactory,
    make_user: UserFactory,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    target = await make_user(home_tenant_id=tenant.id)
    repository = RolesRepository(db_session)
    service = RolesService(repository)
    owner_permissions = await _owner_permissions(repository, owner_role.id)
    source, _ = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Старая роль",
        description=None,
        permission_codes=["catalog.view"],
    )
    replacement, _ = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Новая роль",
        description=None,
        permission_codes=["catalog.view", "pos.sell"],
    )
    assignment = await repository.insert_assignment(
        user_id=target.id,
        tenant_id=tenant.id,
        branch_id=None,
        role_id=source.id,
        password_required=False,
    )

    result = await service.archive_role_with_replacement(
        actor_id=owner.id,
        tenant_id=tenant.id,
        role_id=source.id,
        expected_version=source.version,
        replacement_role_id=replacement.id,
    )

    await db_session.refresh(assignment)
    archived = await repository.get_role_for_update(source.id)
    replacement_version_id = await repository.get_published_role_version_id(replacement.id)
    assert result.affected_memberships == 1
    assert archived is not None and archived.is_active is False
    assert assignment.role_id == replacement.id
    assert assignment.role_version_id == replacement_version_id
    assert (
        not (
            await db_session.execute(
                select(AccessRoleVersion.id).where(
                    AccessRoleVersion.role_id == source.id,
                    AccessRoleVersion.status == "published",
                )
            )
        )
        .scalars()
        .all()
    )


async def test_archive_rejects_replacement_outside_owner_delegation(
    db_session: AsyncSession,
    make_owner: OwnerFactory,
    make_tenant: TenantFactory,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    repository = RolesRepository(db_session)
    service = RolesService(repository)
    owner_permissions = await _owner_permissions(repository, owner_role.id)
    source, _ = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Роль для архива",
        description=None,
        permission_codes=["catalog.view"],
    )
    hidden_replacement = await create_published_test_role(
        db_session,
        tenant_id=tenant.id,
        name="Скрытая роль-замена",
        permission_codes=["tenant.export.full"],
    )
    await db_session.execute(text("SELECT set_config('app.support_access_session_id', '', true)"))
    await set_test_recent_confirmation(db_session, user_id=owner.id)
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(owner.id)},
    )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant.id)},
    )

    visible_versions = await service.list_role_versions(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        role_id=hidden_replacement.id,
    )
    assert visible_versions[0].permissions == ()

    with pytest.raises(PermissionDeniedError, match="недоступна"):
        await service.archive_role_with_replacement(
            actor_id=owner.id,
            tenant_id=tenant.id,
            role_id=source.id,
            expected_version=source.version,
            replacement_role_id=hidden_replacement.id,
        )
