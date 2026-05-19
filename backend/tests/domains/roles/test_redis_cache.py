"""Effective permissions go through a Redis cache; modifications invalidate."""

from __future__ import annotations

import json

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService, perms_cache_key


async def test_cache_populated_on_first_read(
    db_session: AsyncSession,
    redis: Redis,
    make_tenant,
    system_roles,
    make_user,
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="cache-actor@aurum.tj")
    service = RolesService(RolesRepository(db_session), redis=redis)

    user, _, _ = await service.invite_user(
        actor_level=2,
        actor_id=actor.id,
        tenant_id=tenant.id,
        email="cache@aurum.tj",
        full_name="C",
        role_id=system_roles["seller"].id,
        branch_id=None,
        password_required=False,
    )

    # First read populates the cache.
    perms_first = await service.get_effective_permissions(user.id, tenant.id)
    assert "pos.sell" in perms_first

    cached = await redis.get(perms_cache_key(user.id, tenant.id))
    assert cached is not None
    assert "pos.sell" in json.loads(cached)


async def test_assignment_change_invalidates_cache(
    db_session: AsyncSession,
    redis: Redis,
    make_tenant,
    system_roles,
    make_user,
) -> None:
    tenant = await make_tenant()
    actor = await make_user(email="cache-inv-actor@aurum.tj")
    target = await make_user(email="cache-inv-target@aurum.tj")
    service = RolesService(RolesRepository(db_session), redis=redis)

    initial = await service.assign_role(
        actor_level=2,
        actor_id=actor.id,
        tenant_id=tenant.id,
        target_user_id=target.id,
        role_id=system_roles["seller"].id,
        branch_id=None,
        password_required=False,
    )
    perms = await service.get_effective_permissions(target.id, tenant.id)
    assert "users.invite" not in perms
    assert await redis.get(perms_cache_key(target.id, tenant.id)) is not None

    # Revoke the assignment — invalidation must run.
    await service.revoke_assignment(
        actor_level=2,
        tenant_id=tenant.id,
        target_user_id=target.id,
        assignment_id=initial.id,
    )

    assert await redis.get(perms_cache_key(target.id, tenant.id)) is None

    # Re-assign as owner — new perms set, cached on next read.
    await service.assign_role(
        actor_level=2,
        actor_id=actor.id,
        tenant_id=tenant.id,
        target_user_id=target.id,
        role_id=system_roles["owner"].id,
        branch_id=None,
        password_required=False,
    )
    perms_after = await service.get_effective_permissions(target.id, tenant.id)
    assert "users.invite" in perms_after
