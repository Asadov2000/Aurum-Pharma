"""Fixtures for catalog tests."""

from __future__ import annotations

from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService


@pytest_asyncio.fixture
async def make_tenant(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    service = FoundationService(FoundationRepository(db_session))

    async def _make(name: str | None = None) -> Tenant:
        nick = uuid4().hex[:8]
        return await service.create_tenant(
            payload={
                "name": name or f"Tenant {nick}",
                "contact_email": f"t-{nick}@aurum.tj",
            }
        )

    return _make


@pytest_asyncio.fixture
async def make_user(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    async def _make(
        *,
        email: str | None = None,
        home_tenant_id=None,
    ) -> AppUser:
        u = AppUser(
            email=email or f"u-{uuid4().hex[:8]}@aurum.tj",
            full_name="Test User",
            home_tenant_id=home_tenant_id,
            status="active",
        )
        db_session.add(u)
        await db_session.flush()
        await db_session.refresh(u)
        return u

    return _make
