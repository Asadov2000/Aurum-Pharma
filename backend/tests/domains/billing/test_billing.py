"""Legacy subscription state transitions during the ledger migration."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import SubscriptionsDisabledError
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService


async def test_seed_plan_present(db_session: AsyncSession) -> None:
    repo = BillingRepository(db_session)
    plan = await repo.get_plan_by_code("aurum_pharma")
    assert plan is not None
    assert plan.is_active is True
    assert plan.price_per_branch > 0


async def test_new_tenant_does_not_need_or_receive_subscription(
    db_session: AsyncSession, make_tenant_with_plan
) -> None:
    tenant, plan = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    with pytest.raises(SubscriptionsDisabledError):
        await service.create_subscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            billing_period="monthly",
            branches_count=1,
            status="active",
        )
    assert await service.get_active_subscription(tenant.id) is None
