"""HTTP fixture for platform access tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.deps import _seed_request_db_context, get_db
from app.main import app


@pytest_asyncio.fixture
async def platform_client(
    db_session: AsyncSession,
    client: AsyncClient,
) -> AsyncIterator[AsyncClient]:
    async def _override(request: Request) -> AsyncIterator[AsyncSession]:
        await _seed_request_db_context(request, db_session)
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
