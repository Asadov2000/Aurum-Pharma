"""Logout must commit even when optional permission-cache cleanup is unavailable."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from secrets import token_hex
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import deps
from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token, hash_token
from app.core.time import utc_now
from app.domains.auth.models import AppUser, Session
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import TenantMembership
from app.domains.roles.service import perms_cache_key
from app.main import app

BASE = "/api/v1/auth"
FailurePhase = Literal["scan-before", "scan-after-delete", "delete"]
SessionState = tuple[datetime | None, str | None]


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
            raise RuntimeError("Logout commit tests require owned disposable PostgreSQL")
    redis_url = os.environ.get("TEST_REDIS_URL")
    if redis_url is None:
        raise RuntimeError("Logout commit tests require explicit TEST_REDIS_URL")
    parsed_redis = urlparse(redis_url)
    app_database = urlparse(settings.DATABASE_URL_APP)
    allowed_redis = (
        {"redis"}
        if app_database.path == "/aurum_verify_test"
        else {"redis-test", "localhost", "127.0.0.1"}
    )
    if (
        parsed_redis.hostname not in allowed_redis
        or parsed_redis.path != "/0"
        or redis_url != settings.REDIS_URL
    ):
        raise RuntimeError("Logout commit tests require the matching dedicated test Redis")


# Runs at collection, before the shared Redis fixture performs cache cleanup.
_require_isolated_services()


@dataclass(frozen=True)
class LoginSession:
    id: UUID
    user_id: UUID
    tenant_id: UUID
    access: str = field(repr=False)
    refresh: str = field(repr=False)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


@pytest_asyncio.fixture
async def committed_sessions(
    db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[LoginSession, LoginSession, LoginSession]]:
    """Unique committed fixtures live only until the disposable test DB is destroyed.

    Preserve the real request commit/rollback dependencies. Per-test NullPool
    engines avoid sharing asyncpg connections between pytest event loops.
    """
    settings = get_settings()
    app_engine = create_async_engine(settings.DATABASE_URL_APP, poolclass=NullPool)
    monkeypatch.setattr(deps, "AppSessionLocal", async_sessionmaker(app_engine))
    monkeypatch.setattr(deps, "SupportSessionLocal", async_sessionmaker(db_engine))
    assert all(
        dependency not in app.dependency_overrides
        for dependency in (deps.get_db, deps.get_auth_db, deps.get_support_auth_db)
    )
    logins: list[LoginSession] = []
    try:
        async with async_sessionmaker(db_engine, expire_on_commit=False)() as db:
            async with db.begin():
                await db.execute(text("SELECT set_config('app.support_session', 'true', true)"))
                for index in range(2):
                    tenant = await FoundationService(FoundationRepository(db)).create_tenant(
                        payload={
                            "name": f"Logout cache {uuid4().hex}",
                            "contact_email": f"logout-{uuid4().hex}@example.test",
                        }
                    )
                    user = AppUser(
                        email=f"logout-{uuid4().hex}@example.test",
                        full_name="Logout cache test",
                        home_tenant_id=tenant.id,
                        status="active",
                    )
                    db.add(user)
                    await db.flush()
                    db.add(
                        TenantMembership(
                            tenant_id=tenant.id,
                            user_id=user.id,
                            full_name=user.full_name,
                            status="active",
                        )
                    )
                    for _ in range(2 if index == 0 else 1):
                        refresh = token_hex(32)
                        session = Session(
                            user_id=user.id,
                            refresh_token_hash=hash_token(refresh),
                            expires_at=utc_now() + timedelta(days=1),
                        )
                        db.add(session)
                        await db.flush()
                        logins.append(
                            LoginSession(
                                session.id,
                                user.id,
                                tenant.id,
                                create_access_token(
                                    user.id,
                                    tenant_id=tenant.id,
                                    session_id=session.id,
                                    is_developer=False,
                                    is_administrator=False,
                                ),
                                refresh,
                            )
                        )
        yield logins[0], logins[1], logins[2]
    finally:
        await app_engine.dispose()


async def _states(engine: AsyncEngine, user_ids: tuple[UUID, UUID]) -> dict[UUID, SessionState]:
    # Independent connection: a rollback cannot be hidden by an ORM identity map.
    async with async_sessionmaker(engine)() as db:
        async with db.begin():
            await db.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            rows = await db.execute(
                select(Session.id, Session.revoked_at, Session.revoked_reason).where(
                    Session.user_id.in_(user_ids)
                )
            )
            return {sid: (revoked_at, reason) for sid, revoked_at, reason in rows}


def _cookie_headers(refresh: str) -> dict[str, str]:
    settings = get_settings()
    return {
        "Origin": settings.CORS_ORIGINS[0],
        "Cookie": f"{settings.REFRESH_COOKIE_NAME}={refresh}",
    }


def _assert_logout(response: Response) -> None:
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    cookies: SimpleCookie[str] = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    settings = get_settings()
    cookie = cookies[settings.REFRESH_COOKIE_NAME]
    assert cookie.value == ""
    assert cookie["max-age"] == "0"
    assert cookie["path"] == BASE
    assert cookie["httponly"]
    assert bool(cookie["secure"]) == settings.refresh_cookie_secure
    assert cookie["samesite"].lower() == settings.REFRESH_COOKIE_SAMESITE.lower()


async def _rotate_session(
    client: AsyncClient, original: LoginSession, operation_id: UUID
) -> LoginSession:
    response = await client.post(
        f"{BASE}/refresh",
        headers=_cookie_headers(original.refresh),
        json={"operation_id": str(operation_id)},
    )
    assert response.status_code == 200
    access = response.json()["access_token"]
    return LoginSession(
        UUID(str(decode_access_token(access)["sid"])),
        original.user_id,
        original.tenant_id,
        access,
        client.cookies[get_settings().REFRESH_COOKIE_NAME],
    )


async def _assert_revoked_session_isolated(
    client: AsyncClient,
    logins: tuple[LoginSession, LoginSession, LoginSession],
    target: LoginSession,
    operation_id: UUID,
) -> None:
    original, sibling, foreign = logins
    assert (await client.get(f"{BASE}/sessions", headers=target.headers)).status_code == 401
    for login in (sibling, foreign):
        listing = await client.get(f"{BASE}/sessions", headers=login.headers)
        assert listing.status_code == 200
        ids = {UUID(item["id"]) for item in listing.json()["items"]}
        assert login.id in ids
        assert target.id not in ids
    for token in {original.refresh, target.refresh}:
        replay = await client.post(
            f"{BASE}/refresh",
            headers=_cookie_headers(token),
            json={"operation_id": str(operation_id)},
        )
        assert replay.status_code == 401
    repeated = await client.post(
        f"{BASE}/logout",
        headers=_cookie_headers(original.refresh),
        json={"operation_id": str(operation_id)},
    )
    _assert_logout(repeated)


@pytest.mark.parametrize("rotated", (False, True), ids=("direct", "lost-refresh-response"))
@pytest.mark.parametrize(
    ("phase", "error_type"),
    (
        pytest.param(None, None, id="healthy"),
        pytest.param("scan-before", RedisConnectionError, id="scan-connection"),
        pytest.param("scan-before", RedisTimeoutError, id="scan-timeout"),
        pytest.param("scan-after-delete", RedisConnectionError, id="partial-scan-connection"),
        pytest.param("scan-after-delete", RedisTimeoutError, id="partial-scan-timeout"),
        pytest.param("delete", RedisConnectionError, id="delete-connection"),
        pytest.param("delete", RedisTimeoutError, id="delete-timeout"),
    ),
)
async def test_logout_commits_when_permission_cache_cleanup_fails(
    client: AsyncClient,
    redis: Redis,
    db_engine: AsyncEngine,
    committed_sessions: tuple[LoginSession, LoginSession, LoginSession],
    monkeypatch: pytest.MonkeyPatch,
    phase: FailurePhase | None,
    error_type: type[RedisError] | None,
    rotated: bool,
) -> None:
    original, sibling, foreign = committed_sessions
    operation_id = uuid4()
    target = await _rotate_session(client, original, operation_id) if rotated else original
    for login in (target, sibling, foreign):
        assert (await client.get(f"{BASE}/sessions", headers=login.headers)).status_code == 200
    before = await _states(db_engine, (original.user_id, foreign.user_id))
    assert before[target.id] == (None, None)
    target_key = perms_cache_key(original.user_id, original.tenant_id)
    foreign_key = perms_cache_key(foreign.user_id, foreign.tenant_id)
    pattern = f"auth:perms:{original.user_id}:*"
    permissions = '["catalog.view"]'
    await redis.set(target_key, permissions)
    await redis.set(foreign_key, permissions)
    scan = redis.scan_iter
    delete = redis.delete
    faults = 0
    deletions = 0

    async def failing_scan(match: str | None = None, **kwargs: object) -> AsyncIterator[str]:
        nonlocal faults
        if match == pattern and phase == "scan-before" and error_type is not None:
            faults += 1
            raise error_type("Synthetic permission cache failure")
        async for key in scan(match=match, **kwargs):
            yield key
            if match == pattern and phase == "scan-after-delete" and error_type is not None:
                faults += 1
                raise error_type("Synthetic partial permission cache failure")

    async def failing_delete(*keys: str) -> int:
        nonlocal faults, deletions
        if target_key in keys and phase == "delete" and error_type is not None:
            faults += 1
            raise error_type("Synthetic permission cache failure")
        count = await delete(*keys)
        if target_key in keys:
            deletions += count
        return count

    with monkeypatch.context() as patch:
        patch.setattr(redis, "scan_iter", failing_scan)
        patch.setattr(redis, "delete", failing_delete)
        response = await client.post(
            f"{BASE}/logout",
            headers=_cookie_headers(original.refresh),
            json={"operation_id": str(operation_id)},
        )
        _assert_logout(response)
        assert faults > 0 if phase is not None else faults == 0
        if phase == "scan-after-delete":
            assert deletions > 0
        after = await _states(db_engine, (original.user_id, foreign.user_id))
        assert after[target.id][0] is not None
        assert after[target.id][1] == "logout"
        assert {sid: state for sid, state in after.items() if sid != target.id} == {
            sid: state for sid, state in before.items() if sid != target.id
        }
        assert await redis.get(foreign_key) == permissions
        assert await redis.get(target_key) == (
            None if phase in (None, "scan-after-delete") else permissions
        )
        await _assert_revoked_session_isolated(client, committed_sessions, target, operation_id)
        assert await _states(db_engine, (original.user_id, foreign.user_id)) == after
    await _assert_sessions_can_refresh(client, (sibling, foreign))


async def _assert_sessions_can_refresh(
    client: AsyncClient, logins: tuple[LoginSession, LoginSession]
) -> None:
    for login in logins:
        refreshed = await client.post(
            f"{BASE}/refresh",
            headers=_cookie_headers(login.refresh),
            json={"operation_id": str(uuid4())},
        )
        assert refreshed.status_code == 200
