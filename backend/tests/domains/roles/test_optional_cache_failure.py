"""Optional cache failures cannot roll back the HTTP authorization transaction.

Committed synthetic fixtures and fresh aurum_app connections exercise the real
request dependency. This module runs only against disposable test services.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core import deps
from app.core.config import get_settings
from app.core.security import create_access_token, hash_token
from app.core.time import utc_now
from app.domains.auth.models import AppUser, Session
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import TenantMembership, UserAssignment
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import perms_cache_key
from app.main import app
from tests.role_version_helpers import create_published_test_role, provision_test_owner


def _require_isolated_services() -> None:
    settings = get_settings()
    allowed_databases = {
        ("postgres", "aurum_verify_test"),
        ("postgres-test", "aurum_test"),
        ("localhost", "aurum_test"),
        ("127.0.0.1", "aurum_test"),
    }
    for url in (settings.DATABASE_URL_APP, settings.DATABASE_URL_SUPPORT):
        parsed = urlparse(url)
        if (parsed.hostname, parsed.path.removeprefix("/")) not in allowed_databases:
            raise RuntimeError("Employee commit tests require owned disposable PostgreSQL")
    redis_url = os.environ.get("TEST_REDIS_URL")
    if redis_url is None:
        raise RuntimeError("Employee commit tests require explicit TEST_REDIS_URL")
    parsed_redis = urlparse(redis_url)
    allowed_redis = (
        {"redis"}
        if urlparse(settings.DATABASE_URL_APP).path == "/aurum_verify_test"
        else {"redis-test", "localhost", "127.0.0.1"}
    )
    if (
        parsed_redis.hostname not in allowed_redis
        or parsed_redis.path != "/0"
        or redis_url != settings.REDIS_URL
    ):
        raise RuntimeError("Employee commit tests require matching dedicated test Redis")


# Reject shared services before Redis fixtures can perform test cleanup.
_require_isolated_services()


@dataclass(frozen=True)
class EmployeeFixture:
    target_id: UUID
    tenant_id: UUID
    owner_id: UUID
    role_id: UUID
    assignment_id: UUID
    foreign_id: UUID
    foreign_tenant_id: UUID
    owner_headers: dict[str, str] = field(repr=False)
    target_headers: dict[str, str] = field(repr=False)
    sibling_headers: dict[str, str] = field(repr=False)
    foreign_headers: dict[str, str] = field(repr=False)


async def _headers(db: AsyncSession, user: AppUser) -> dict[str, str]:
    session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(str(uuid4())),
        expires_at=utc_now() + timedelta(hours=1),
        password_verified_at=utc_now(),
    )
    db.add(session)
    await db.flush()
    return {
        "Authorization": "Bearer "
        + create_access_token(
            user.id,
            tenant_id=user.home_tenant_id,
            is_developer=False,
            is_administrator=False,
            session_id=session.id,
            password_verified_at=session.password_verified_at,
        )
    }


async def _employee(db: AsyncSession, tenant_id: UUID) -> AppUser:
    user = AppUser(
        email=f"cache-employee-{uuid4().hex}@example.test",
        full_name="Cache test employee",
        home_tenant_id=tenant_id,
        status="active",
    )
    db.add(user)
    await db.flush()
    db.add(
        TenantMembership(
            tenant_id=tenant_id, user_id=user.id, full_name=user.full_name, status="active"
        )
    )
    await db.flush()
    return user


async def _seed(db: AsyncSession) -> EmployeeFixture:
    foundation = FoundationService(FoundationRepository(db))
    tenant = await foundation.create_tenant(
        payload={
            "name": f"Cache {uuid4().hex}",
            "contact_email": f"cache-{uuid4().hex}@example.test",
        }
    )
    owner, _, _, _ = await provision_test_owner(
        db,
        tenant_id=tenant.id,
        email=f"cache-owner-{uuid4().hex}@example.test",
        full_name="Cache test owner",
    )
    target = await _employee(db, tenant.id)
    sibling = await _employee(db, tenant.id)
    repository = RolesRepository(db)
    role = await create_published_test_role(
        db, tenant_id=tenant.id, name="Cache failure target", permission_codes=["catalog.view"]
    )
    assignment = await repository.insert_assignment(
        user_id=target.id,
        tenant_id=tenant.id,
        branch_id=None,
        role_id=role.id,
        password_required=False,
    )
    sibling_role = await create_published_test_role(
        db, tenant_id=tenant.id, name="Cache failure unaffected", permission_codes=["catalog.view"]
    )
    await repository.insert_assignment(
        user_id=sibling.id,
        tenant_id=tenant.id,
        branch_id=None,
        role_id=sibling_role.id,
        password_required=False,
    )
    foreign_tenant = await foundation.create_tenant(
        payload={
            "name": f"Cache foreign {uuid4().hex}",
            "contact_email": f"cache-foreign-{uuid4().hex}@example.test",
        }
    )
    foreign, _, _, _ = await provision_test_owner(
        db,
        tenant_id=foreign_tenant.id,
        email=f"cache-foreign-owner-{uuid4().hex}@example.test",
        full_name="Foreign cache owner",
    )
    return EmployeeFixture(
        target.id,
        tenant.id,
        owner.id,
        role.id,
        assignment.id,
        foreign.id,
        foreign_tenant.id,
        await _headers(db, owner),
        await _headers(db, target),
        await _headers(db, sibling),
        await _headers(db, foreign),
    )


@pytest_asyncio.fixture
async def committed_employee(
    db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[EmployeeFixture, AsyncEngine]]:
    # Synthetic rows remain only until the owned disposable database is destroyed.
    settings = get_settings()
    app_engine = create_async_engine(settings.DATABASE_URL_APP, poolclass=NullPool)
    monkeypatch.setattr(deps, "AppSessionLocal", async_sessionmaker(app_engine))
    monkeypatch.setattr(deps, "SupportSessionLocal", async_sessionmaker(db_engine))
    assert deps.get_db not in app.dependency_overrides
    try:
        async with async_sessionmaker(db_engine, expire_on_commit=False).begin() as db:
            await db.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            seeded = await _seed(db)
        yield seeded, app_engine
    finally:
        await app_engine.dispose()


async def _assert_committed(engine: AsyncEngine, seeded: EmployeeFixture, operation: str) -> None:
    # NullPool opens a new physical aurum_app connection after the HTTP commit.
    async with async_sessionmaker(engine).begin() as check:
        assert await check.scalar(text("SELECT session_user")) == "aurum_app"
        await check.execute(
            text(
                "SELECT set_config('app.user_id', :actor, true), "
                "set_config('app.tenant_id', :tenant, true), "
                "set_config('app.support_session', 'false', true)"
            ),
            {"actor": str(seeded.owner_id), "tenant": str(seeded.tenant_id)},
        )
        membership = (
            await check.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == seeded.tenant_id,
                    TenantMembership.user_id == seeded.target_id,
                )
            )
        ).scalar_one()
        snapshot = await RolesRepository(check).authorization_snapshot(
            seeded.target_id, seeded.tenant_id
        )
        assert membership.status == ("suspended" if operation == "block" else "active")
        assert "catalog.view" not in snapshot.permissions
        if operation == "revoke":
            assignment = await check.get(UserAssignment, seeded.assignment_id)
            assert assignment is not None and not assignment.is_active


async def _assert_access(client: AsyncClient, seeded: EmployeeFixture, operation: str) -> None:
    denied = await client.get("/api/v1/catalog", headers=seeded.target_headers)
    assert denied.status_code == (401 if operation == "block" else 403)
    for headers in [seeded.sibling_headers, seeded.foreign_headers]:
        assert (await client.get("/api/v1/catalog", headers=headers)).status_code == 200
    if operation == "block":
        restored = await client.patch(
            f"/api/v1/users/{seeded.target_id}",
            headers=seeded.owner_headers,
            json={"status": "active"},
        )
        assert restored.status_code == 200, restored.text
        assert (
            await client.get("/api/v1/catalog", headers=seeded.target_headers)
        ).status_code == 401


@pytest.mark.parametrize("failure_type", [RedisConnectionError, RedisTimeoutError])
@pytest.mark.parametrize("operation", ["block", "revoke", "publish"])
async def test_optional_delete_failure_preserves_security_mutation(
    redis: Redis,
    committed_employee: tuple[EmployeeFixture, AsyncEngine],
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[RedisError],
    operation: Literal["block", "revoke", "publish"],
) -> None:
    seeded, engine = committed_employee
    target_id, tenant_id, role_id, assignment_id = (
        seeded.target_id,
        seeded.tenant_id,
        seeded.role_id,
        seeded.assignment_id,
    )
    owner_headers, target_headers = seeded.owner_headers, seeded.target_headers
    key = perms_cache_key(target_id, tenant_id)
    foreign_key = perms_cache_key(seeded.foreign_id, seeded.foreign_tenant_id)
    stale = '["catalog.view"]'
    await redis.set(key, stale)
    await redis.set(foreign_key, stale)
    original_delete = redis.delete
    faults = 0

    async def failing_delete(*keys: str) -> int:
        nonlocal faults
        if key in keys:
            faults += 1
            raise failure_type("Synthetic optional permission-cache failure")
        return await original_delete(*keys)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            assert (await client.get("/api/v1/catalog", headers=target_headers)).status_code == 200
            with monkeypatch.context() as patch:
                patch.setattr(redis, "delete", failing_delete)
                if operation == "block":
                    response = await client.post(
                        f"/api/v1/users/{target_id}/block", headers=owner_headers
                    )
                elif operation == "revoke":
                    response = await client.delete(
                        f"/api/v1/users/{target_id}/assignments/{assignment_id}",
                        headers=owner_headers,
                    )
                else:
                    response = await client.patch(
                        f"/api/v1/roles/{role_id}",
                        headers=owner_headers,
                        json={"expected_version": 1, "permissions": []},
                    )
                assert faults > 0
                assert response.status_code == 200, response.text
                assert await redis.get(key) == stale

            await _assert_committed(engine, seeded, operation)
            await _assert_access(client, seeded, operation)
            assert await redis.get(foreign_key) == stale
    finally:
        await redis.delete(key, foreign_key)
