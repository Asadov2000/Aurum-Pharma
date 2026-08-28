"""Fixtures for foundation tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.deps import _seed_request_db_context, get_db
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import (
    TenantMembership,
    UserAssignment,
)
from app.main import app
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import create_test_platform_user
from tests.role_version_helpers import create_published_test_role, provision_test_owner


@pytest_asyncio.fixture
async def auth_client(
    db_session: AsyncSession,
    client: AsyncClient,
) -> AsyncIterator[AsyncClient]:
    async def _override(request: Request) -> AsyncIterator[AsyncSession]:
        for key in (
            "app.user_id",
            "app.tenant_id",
            "app.support_access_session_id",
            "app.auth_session_id",
            "app.mfa_verified_at",
        ):
            await db_session.execute(
                text("SELECT set_config(:key, '', true)"),
                {"key": key},
            )
        await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
        await _seed_request_db_context(request, db_session)
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
        if is_developer or is_administrator:
            if is_developer and is_administrator:
                raise ValueError("A test platform account must have one access kind")
            if home_tenant_id is not None:
                raise ValueError("A platform account cannot have a tenant")
            return await create_test_platform_user(
                db_session,
                access_kind="developer" if is_developer else "administrator",
                email=email,
                status=status,
            )
        u = AppUser(
            email=email or f"u-{uuid4().hex[:8]}@aurum.tj",
            full_name="Test User",
            home_tenant_id=home_tenant_id,
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
    """Create a tenant user; owners are the default for settings tests."""

    async def _factory(
        tenant: Tenant | None = None,
        *,
        is_owner: bool = True,
        permission_codes: tuple[str, ...] = (),
    ) -> tuple[str, Tenant, AppUser]:
        t = tenant if tenant is not None else await make_tenant()
        if is_owner:
            owner, _membership, _ownership, _role = await provision_test_owner(
                db_session,
                tenant_id=t.id,
                email=f"settings-owner-{uuid4().hex[:8]}@aurum.tj",
                full_name="Settings Owner",
            )
            token = create_access_token(
                owner.id,
                tenant_id=t.id,
                is_developer=False,
                is_administrator=False,
            )
            await db_session.execute(
                text("SELECT set_config('app.support_session', 'false', true)")
            )
            await db_session.execute(
                text("SELECT set_config('app.support_access_session_id', '', true)")
            )
            await db_session.execute(text("SELECT set_config('app.auth_session_id', '', true)"))
            await db_session.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": str(owner.id)},
            )
            await db_session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(t.id)},
            )
            return token, t, owner

        user = await make_user(home_tenant_id=t.id)
        membership = TenantMembership(
            tenant_id=t.id,
            user_id=user.id,
            full_name=user.full_name,
            status="active",
        )
        role = await create_published_test_role(
            db_session,
            tenant_id=t.id,
            name=f"Settings administrator {uuid4().hex[:8]}",
            permission_codes=permission_codes,
            level=2,
        )
        db_session.add(membership)
        await db_session.flush()
        await db_session.refresh(membership)
        db_session.add(
            UserAssignment(
                user_id=user.id,
                tenant_id=t.id,
                membership_id=membership.id,
                role_id=role.id,
            )
        )
        await db_session.flush()
        await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
        await db_session.execute(
            text("SELECT set_config('app.support_access_session_id', '', true)")
        )
        await db_session.execute(text("SELECT set_config('app.auth_session_id', '', true)"))
        await db_session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(user.id)},
        )
        token = create_access_token(
            user.id,
            tenant_id=t.id,
            is_developer=False,
            is_administrator=False,
        )
        return token, t, user

    return _factory
