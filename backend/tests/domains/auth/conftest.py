"""Fixtures shared by the auth domain tests.

`auth_client` reuses the SAVEPOINT-wrapped `db_session` from the top-level
conftest by overriding FastAPI's `get_db` dependency — that way the HTTP
handler sees the same in-flight transaction as the test, and nothing leaks
between tests.

We also force Celery into eager mode so `send_email_code.delay(...)` runs
inline instead of trying to talk to the broker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import hash_password
from app.domains.auth.models import AppUser
from app.main import app
from app.tasks.celery_app import celery_app


@pytest_asyncio.fixture(autouse=True)
async def celery_eager() -> AsyncIterator[None]:
    prev = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = prev


@pytest_asyncio.fixture
async def auth_client(
    db_session: AsyncSession,
    client: AsyncClient,
) -> AsyncIterator[AsyncClient]:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def make_user(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """Factory that inserts an AppUser and returns it. Email is unique per call."""

    async def _make(
        *,
        email: str | None = None,
        full_name: str = "Test User",
        password: str | None = None,
        is_developer: bool = False,
        is_administrator: bool = False,
        home_tenant_id: UUID | None = None,
        status: str = "active",
    ) -> AppUser:
        u = AppUser(
            email=email or f"user-{uuid4().hex[:8]}@aurum.tj",
            full_name=full_name,
            password_hash=hash_password(password) if password else None,
            is_developer=is_developer,
            is_administrator=is_administrator,
            home_tenant_id=home_tenant_id,
            status=status,
        )
        db_session.add(u)
        await db_session.flush()
        await db_session.refresh(u)
        return u

    return _make
