"""Fixtures for foundation tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.main import app


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
async def make_tenant(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """Insert a tenant + default settings, return the Tenant row."""

    service = FoundationService(FoundationRepository(db_session))

    async def _make(
        *,
        name: str | None = None,
        contact_email: str | None = None,
        status: str = "active",
    ) -> Tenant:
        nick = uuid4().hex[:8]
        payload: dict[str, object] = {
            "name": name or f"Tenant {nick}",
            "contact_email": contact_email or f"tenant-{nick}@aurum.tj",
        }
        tenant = await service.create_tenant(payload=payload)
        if status != "setup":
            await service.update_tenant(tenant.id, fields={"status": status})
            await db_session.refresh(tenant)
        return tenant

    return _make


@pytest_asyncio.fixture
async def make_user(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """Insert an AppUser, optionally with developer / administrator flags."""

    async def _make(
        *,
        email: str | None = None,
        home_tenant_id: UUID | None = None,
        is_developer: bool = False,
        is_administrator: bool = False,
        status: str = "active",
    ) -> AppUser:
        u = AppUser(
            email=email or f"u-{uuid4().hex[:8]}@aurum.tj",
            full_name="Test User",
            home_tenant_id=home_tenant_id,
            is_developer=is_developer,
            is_administrator=is_administrator,
            status=status,
        )
        db_session.add(u)
        await db_session.flush()
        await db_session.refresh(u)
        return u

    return _make


@pytest_asyncio.fixture
async def support_token(make_user) -> str:  # type: ignore[no-untyped-def]
    """An access token for a freshly-minted administrator."""
    admin = await make_user(is_administrator=True)
    return create_access_token(
        admin.id,
        tenant_id=None,
        is_developer=False,
        is_administrator=True,
    )


@pytest_asyncio.fixture
async def tenant_admin_token(make_tenant, make_user):  # type: ignore[no-untyped-def]
    """A token for a tenant-scoped administrator (so RLS does see a tenant_id
    in the JWT, and permission checks pass because of is_administrator)."""

    async def _factory(tenant: Tenant | None = None) -> tuple[str, Tenant, AppUser]:
        t = tenant if tenant is not None else await make_tenant()
        user = await make_user(home_tenant_id=t.id, is_administrator=True)
        token = create_access_token(
            user.id,
            tenant_id=t.id,
            is_developer=False,
            is_administrator=True,
        )
        return token, t, user

    return _factory
