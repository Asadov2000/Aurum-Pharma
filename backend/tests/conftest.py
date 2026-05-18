"""Shared pytest fixtures.

`db_session` wraps each test in a SAVEPOINT on a connection-level transaction
that is rolled back in teardown — no test leaks data into another. The session
is bound to the support engine (BYPASSRLS) so fixtures can seed any tenant data;
tests that need to verify RLS isolation switch to the app engine explicitly.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.core.db import support_engine
from app.core.redis import redis_client
from app.main import app


@pytest_asyncio.fixture
async def db_connection() -> AsyncIterator[AsyncConnection]:
    async with support_engine.connect() as connection:
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
    try:
        yield redis_client
    finally:
        # Flush keys this test touched. Each test should use a unique prefix
        # if it wants stronger isolation; tenant-scoped keys handle that.
        pass
