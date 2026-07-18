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
from app.domains.roles.models import (
    Role,
    RolePermission,
    TenantMembership,
    UserAssignment,
)
from app.main import app
from tests.auth_helpers import create_support_access_token


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
async def support_token(
    db_session: AsyncSession,
    make_user,
) -> str:  # type: ignore[no-untyped-def]
    """An access token for a freshly-minted administrator."""
    admin = await make_user(is_administrator=True)
    return await create_support_access_token(db_session, admin)


@pytest_asyncio.fixture
async def tenant_admin_token(
    db_session: AsyncSession,
    make_tenant,
    make_user,
):  # type: ignore[no-untyped-def]
    """Create a tenant-scoped administrator with explicit settings access."""

    async def _factory(tenant: Tenant | None = None) -> tuple[str, Tenant, AppUser]:
        t = tenant if tenant is not None else await make_tenant()
        user = await make_user(home_tenant_id=t.id)
        membership = TenantMembership(
            tenant_id=t.id,
            user_id=user.id,
            full_name=user.full_name,
            status="active",
        )
        role = Role(
            tenant_id=t.id,
            name=f"Settings administrator {uuid4().hex[:8]}",
            level=2,
            is_system=False,
        )
        db_session.add_all([membership, role])
        await db_session.flush()
        await db_session.refresh(membership)
        await db_session.refresh(role)
        db_session.add(RolePermission(role_id=role.id, permission_code="settings.update"))
        db_session.add(
            UserAssignment(
                user_id=user.id,
                tenant_id=t.id,
                membership_id=membership.id,
                role_id=role.id,
            )
        )
        await db_session.flush()
        token = create_access_token(
            user.id,
            tenant_id=t.id,
            is_developer=False,
            is_administrator=False,
        )
        return token, t, user

    return _factory
