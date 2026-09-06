"""Shared pytest fixtures.

`db_session` wraps each test in a SAVEPOINT on a connection-level transaction
that is rolled back in teardown — no test leaks data into another. A fresh
async engine is built per-test with NullPool, sidestepping pytest-asyncio's
"Event loop is closed" trap (the module-level pooled engine survives across
loops and the second use blows up). The Redis client is per-test for the
same reason — and the `get_redis` dependency is overridden so the FastAPI
app pulls the same per-test client when handling HTTP requests.

`client` exposes the FastAPI app over an ASGI transport for HTTP-level tests.
Most domain tests will also reach for the per-domain `auth_client` fixture
which overrides `get_db` to share the same in-flight SAVEPOINT.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis, from_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from tests.fixture_helpers import clear_test_auth_redis, resolve_test_redis_url

_LOCAL_TEST_DATABASE_URL_APP = (
    "postgresql+asyncpg://aurum_app:aurum_app_pw@postgres-test:5432/aurum_test"
)
_LOCAL_TEST_DATABASE_URL_SUPPORT = (
    "postgresql+asyncpg://aurum_support:aurum_support_pw@postgres-test:5432/aurum_test"
)
_LOCAL_TEST_DATABASE_URL_MIGRATION = (
    "postgresql+asyncpg://aurum_migrator:aurum_migrator_pw@postgres-test:5432/aurum_test"
)
_LOCAL_TEST_DATABASE_URL_MAILER = (
    "postgresql+asyncpg://aurum_mailer:aurum_mailer_pw@postgres-test:5432/aurum_test"
)
_LOCAL_TEST_DATABASE_URL_BILLING_WORKER = (
    "postgresql+asyncpg://aurum_billing_worker:aurum_billing_worker_pw@"
    "postgres-test:5432/aurum_test"
)
_LOCAL_TEST_DATABASE_URL_WORKER = (
    "postgresql+asyncpg://aurum_worker:aurum_worker_pw@" "postgres-test:5432/aurum_test"
)


def _require_disposable_test_database(url: str, variable_name: str) -> None:
    database_name = urlparse(url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        raise RuntimeError(f"{variable_name} must point to a database ending in '_test'")


_test_database_url_app = os.getenv("TEST_DATABASE_URL_APP", _LOCAL_TEST_DATABASE_URL_APP)
_test_database_url_support = os.getenv(
    "TEST_DATABASE_URL_SUPPORT", _LOCAL_TEST_DATABASE_URL_SUPPORT
)
_test_database_url_migration = os.getenv(
    "TEST_DATABASE_URL_MIGRATION", _LOCAL_TEST_DATABASE_URL_MIGRATION
)
_test_database_url_mailer = os.getenv("TEST_DATABASE_URL_MAILER", _LOCAL_TEST_DATABASE_URL_MAILER)
_test_database_url_billing_worker = os.getenv(
    "TEST_DATABASE_URL_BILLING_WORKER",
    _LOCAL_TEST_DATABASE_URL_BILLING_WORKER,
)
_test_database_url_worker = os.getenv(
    "TEST_DATABASE_URL_WORKER",
    _LOCAL_TEST_DATABASE_URL_WORKER,
)
_require_disposable_test_database(_test_database_url_app, "TEST_DATABASE_URL_APP")
_require_disposable_test_database(_test_database_url_support, "TEST_DATABASE_URL_SUPPORT")
_require_disposable_test_database(
    _test_database_url_migration,
    "TEST_DATABASE_URL_MIGRATION",
)
_require_disposable_test_database(_test_database_url_mailer, "TEST_DATABASE_URL_MAILER")
_require_disposable_test_database(
    _test_database_url_billing_worker,
    "TEST_DATABASE_URL_BILLING_WORKER",
)
_require_disposable_test_database(
    _test_database_url_worker,
    "TEST_DATABASE_URL_WORKER",
)

# Configure the application before importing it: every engine created during
# test collection must point at the disposable test database, never shared dev.
os.environ["DATABASE_URL_APP"] = _test_database_url_app
os.environ["DATABASE_URL_SUPPORT"] = _test_database_url_support
os.environ["DATABASE_URL_MIGRATION"] = _test_database_url_migration
os.environ["DATABASE_URL_MAILER"] = _test_database_url_mailer
os.environ["DATABASE_URL_BILLING_WORKER"] = _test_database_url_billing_worker
os.environ["DATABASE_URL_WORKER"] = _test_database_url_worker
# Never inherit the application's Redis endpoint: cleanup belongs exclusively
# to dedicated test infrastructure (CI can supply TEST_REDIS_URL explicitly).
os.environ["REDIS_URL"] = resolve_test_redis_url(os.environ)
# Unit and integration tests exercise the production-grade lockout policy even
# though the rest of the test stack uses ENVIRONMENT=development.
os.environ["AUTH_LOCAL_TESTING_MODE"] = "false"


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    from app.core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL_SUPPORT,
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def maintenance_engine() -> AsyncIterator[AsyncEngine]:
    """Owner-scoped engine used only for test teardown and global state repair."""

    engine = create_async_engine(
        _test_database_url_migration,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": "aurum_schema_owner"}},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_connection(
    db_engine: AsyncEngine,
) -> AsyncIterator[AsyncConnection]:
    async with db_engine.connect() as connection:
        outer = await connection.begin()
        try:
            yield connection
        finally:
            if outer.is_active:
                await outer.rollback()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    nested = await db_connection.begin_nested()
    await db_connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
    await db_connection.execute(
        text("SELECT set_config('app.mfa_verified_at', :verified_at, true)"),
        {"verified_at": str(int(datetime.now(UTC).timestamp()))},
    )
    session_factory = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
    if nested.is_active:
        await nested.rollback()


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[Redis]:
    """Per-test Redis connection. Returned to the pool on teardown.

    Also overrides FastAPI's `get_redis` dependency so HTTP handlers in the
    same test reuse the same client (otherwise they'd hit the module-level
    `redis_client` which keeps state across test loops)."""
    from app.core.config import get_settings
    from app.core.deps import get_redis
    from app.main import app

    settings = get_settings()
    client = from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)

    async def _override() -> Redis:
        return client

    app.dependency_overrides[get_redis] = _override
    try:
        await clear_test_auth_redis(client)
        yield client
    finally:
        app.dependency_overrides.pop(get_redis, None)
        await client.aclose()


@pytest_asyncio.fixture
async def client(redis: Redis) -> AsyncIterator[AsyncClient]:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
