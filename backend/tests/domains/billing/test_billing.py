"""Legacy subscription state transitions during the ledger migration."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.billing.models import TenantSubscription
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService


async def test_seed_plan_present(db_session: AsyncSession) -> None:
    repo = BillingRepository(db_session)
    plan = await repo.get_plan_by_code("aurum_pharma")
    assert plan is not None
    assert plan.is_active is True
    assert plan.price_per_branch > 0


async def test_get_active_subscription(db_session: AsyncSession, make_tenant_with_plan) -> None:
    tenant, plan = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    await service.create_subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=1,
        status="active",
    )
    row = await service.get_active_subscription(tenant.id)
    assert row is not None
    assert row["plan_code"] == "aurum_pharma"
    assert row["status"] == "active"


# Mute unused-import warnings
_ = TenantSubscription
