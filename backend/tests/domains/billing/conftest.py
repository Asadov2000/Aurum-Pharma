"""Fixtures for billing tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.deps import _seed_request_db_context, get_db
from app.domains.billing.repository import BillingRepository
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
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


@pytest_asyncio.fixture
async def make_tenant_with_plan(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    foundation = FoundationService(FoundationRepository(db_session))
    billing_repo = BillingRepository(db_session)

    async def _make():
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={
                "name": f"Tenant {nick}",
                "contact_email": f"t-{nick}@aurum.tj",
            }
        )
        plan = await billing_repo.get_plan_by_code("aurum_pharma")
        assert plan is not None, "seed plan must exist"
        return tenant, plan

    return _make
