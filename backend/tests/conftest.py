"""Shared pytest fixtures.

`db_session` wraps each test in a SAVEPOINT on a connection-level transaction
that is rolled back in teardown — no test leaks data into another. A fresh
async engine is built per-test with NullPool, sidestepping pytest-asyncio's
"Event loop is closed" trap (the module-level pooled engine survives across
loops and the second use blows up).

`client` exposes the FastAPI app over an ASGI transport for HTTP-level tests.
Most domain tests will also reach for the per-domain `auth_client` fixture
which overrides `get_db` to share the same in-flight SAVEPOINT.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.redis import redis_client
from app.main import app


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
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
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[Redis]:
    yield redis_client
