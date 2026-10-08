"""Optional cache failures never hide database, programming or cancellation errors."""

from __future__ import annotations

from asyncio import CancelledError
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService, perms_cache_key


@pytest.mark.parametrize("scope", ["single", "bulk", "all_tenants"])
@pytest.mark.parametrize("error_type", [RuntimeError, SQLAlchemyError, CancelledError])
async def test_cleanup_only_handles_redis_errors(
    db_session: AsyncSession,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    error_type: type[BaseException],
) -> None:
    user_id, tenant_id = uuid4(), uuid4()
    await redis.set(perms_cache_key(user_id, tenant_id), "[]")
    service = RolesService(RolesRepository(db_session), redis=redis)
    monkeypatch.setattr(redis, "delete", AsyncMock(side_effect=error_type("cleanup interrupted")))

    with pytest.raises(error_type):
        if scope == "single":
            await service.invalidate_user_perms(user_id, tenant_id)
        elif scope == "bulk":
            await service.invalidate_users_perms([user_id], tenant_id)
        else:
            await service.invalidate_user_perms_all_tenants(user_id)
