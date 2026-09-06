"""Fixtures shared by the auth domain tests.

`auth_client` reuses the SAVEPOINT-wrapped `db_session` from the top-level
conftest by overriding FastAPI's `get_db` dependency — that way the HTTP
handler sees the same in-flight transaction as the test, and nothing leaks
between tests.

Celery remains eager for auth-domain maintenance tasks used by tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_auth_db, get_db, get_support_auth_db
from app.core.security import hash_password
from app.domains.auth.models import AppUser
from app.main import app
from app.tasks.celery_app import celery_app
from tests.fixture_helpers import eager_tasks
from tests.platform_access_helpers import create_test_platform_user


@pytest_asyncio.fixture(autouse=True)
async def celery_eager() -> AsyncIterator[None]:
    with eager_tasks(celery_app.conf):
        yield


@pytest_asyncio.fixture
async def auth_client(
    db_session: AsyncSession,
    client: AsyncClient,
) -> AsyncIterator[AsyncClient]:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_auth_db] = _override
    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[get_support_auth_db] = _override
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_auth_db, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_support_auth_db, None)


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
        if is_developer or is_administrator:
            if is_developer and is_administrator:
                raise ValueError("A test platform account must have one access kind")
            if home_tenant_id is not None:
                raise ValueError("A platform account cannot have a tenant")
            return await create_test_platform_user(
                db_session,
                access_kind="developer" if is_developer else "administrator",
                email=email,
                full_name=full_name,
                password_hash=hash_password(password) if password else None,
                status=status,
            )
        u = AppUser(
            email=email or f"user-{uuid4().hex[:8]}@aurum.tj",
            full_name=full_name,
            password_hash=hash_password(password) if password else None,
            home_tenant_id=home_tenant_id,
            status=status,
        )
        db_session.add(u)
        await db_session.flush()
        await db_session.refresh(u)
        return u

    return _make
