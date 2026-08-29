"""Legacy permission-cache keys are ignored and cleaned by mutations."""

from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService, perms_cache_key


async def test_stale_cache_is_never_used_for_authorization(
    db_session: AsyncSession,
    redis: Redis,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="cache-actor@aurum.tj")
    user = await make_user(
        email="cache@aurum.tj",
        full_name="C",
        home_tenant_id=tenant.id,
    )
    seller_role = await make_tenant_role(tenant_id=tenant.id, template_name="Кассир", level=4)
    service = RolesService(RolesRepository(db_session), redis=redis)

    await service.assign_role(
        actor_id=actor.id,
        actor_permissions=set(),
        actor_permission_scopes={},
        actor_is_developer=True,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        target_user_id=user.id,
        role_id=seller_role.id,
        branch_id=None,
        password_required=False,
    )

    key = perms_cache_key(user.id, tenant.id)
    await redis.set(key, '["users.delete"]')

    perms_first = await service.get_effective_permissions(user.id, tenant.id)
    assert "pos.sell" in perms_first
    assert "users.delete" not in perms_first
    assert await redis.get(key) == '["users.delete"]'


async def test_assignment_change_invalidates_cache(
    db_session: AsyncSession,
    redis: Redis,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="cache-inv-actor@aurum.tj")
    target = await make_user(
        email="cache-inv-target@aurum.tj",
        home_tenant_id=tenant.id,
    )
    seller_role = await make_tenant_role(tenant_id=tenant.id, template_name="Кассир", level=4)
    service = RolesService(RolesRepository(db_session), redis=redis)
    elevated_role, _permissions = await service.create_role(
        actor_id=actor.id,
        actor_permissions=set(),
        actor_is_developer=True,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Cache elevated role",
        description=None,
        permission_codes=["users.view"],
    )

    initial = await service.assign_role(
        actor_id=actor.id,
        actor_permissions=set(),
        actor_permission_scopes={},
        actor_is_developer=True,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        target_user_id=target.id,
        role_id=seller_role.id,
        branch_id=None,
        password_required=False,
    )
    perms = await service.get_effective_permissions(target.id, tenant.id)
    assert "users.view" not in perms
    await redis.set(perms_cache_key(target.id, tenant.id), '["users.view"]')

    # Revoke the assignment — invalidation must run.
    await service.revoke_assignment(
        actor_id=actor.id,
        actor_permissions=set(),
        actor_permission_scopes={},
        actor_is_developer=True,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        target_user_id=target.id,
        assignment_id=initial.id,
    )

    assert await redis.get(perms_cache_key(target.id, tenant.id)) is None

    # Re-assign with a different role: reads still come from the database.
    await service.assign_role(
        actor_id=actor.id,
        actor_permissions=set(),
        actor_permission_scopes={},
        actor_is_developer=True,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        target_user_id=target.id,
        role_id=elevated_role.id,
        branch_id=None,
        password_required=False,
    )
    perms_after = await service.get_effective_permissions(target.id, tenant.id)
    assert "users.view" in perms_after


async def test_role_permission_change_invalidates_assigned_users_cache(
    db_session: AsyncSession,
    redis: Redis,
    make_owner,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    target = await make_user(
        email="cache-role-target@aurum.tj",
        home_tenant_id=tenant.id,
    )
    service = RolesService(RolesRepository(db_session), redis=redis)
    owner_permissions = set(await service.repo.get_role_permissions(owner_role.id))
    role, _permissions = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        name="Cache mutable role",
        description=None,
        permission_codes=["pos.sell"],
    )

    await service.assign_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_permission_scopes={code: None for code in owner_permissions},
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        target_user_id=target.id,
        role_id=role.id,
        branch_id=None,
        password_required=False,
    )
    perms = await service.get_effective_permissions(target.id, tenant.id)
    assert "pos.sell" in perms
    await redis.set(perms_cache_key(target.id, tenant.id), '["pos.sell"]')

    await service.update_role(
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

    assert await redis.get(perms_cache_key(target.id, tenant.id)) is None
    perms_after = await service.get_effective_permissions(target.id, tenant.id)
    assert "catalog.view" in perms_after
    assert "pos.sell" not in perms_after
