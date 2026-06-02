"""Fixtures for dashboard tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.main import app


@pytest_asyncio.fixture
async def auth_client(
    db_session: AsyncSession,
    client: AsyncClient,
) -> AsyncIterator[AsyncClient]:
    """ASGI client whose requests share the test's in-flight SAVEPOINT session."""

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
