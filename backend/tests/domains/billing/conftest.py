"""Fixtures for billing tests."""

from __future__ import annotations

from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.billing.repository import BillingRepository
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService


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
