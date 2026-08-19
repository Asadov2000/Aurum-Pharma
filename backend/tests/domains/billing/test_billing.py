"""Legacy subscription state transitions during the ledger migration."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.billing.models import TenantSubscription
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService
from app.domains.foundation.models import Tenant


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


async def test_trial_to_grace_transition(db_session: AsyncSession, make_tenant_with_plan) -> None:
    tenant, plan = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    sub = await service.create_subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=1,
        status="trial",
    )
    # Back-date period_end via the repo so process_trial_endings picks it up.
    past = utc_now() - timedelta(hours=1)
    await db_session.execute(
        text("UPDATE tenant_subscription SET period_end = :ts WHERE id = :id"),
        {"ts": past, "id": sub.id},
    )
    await db_session.flush()

    moved = await service.process_trial_endings()
    assert moved >= 1
    await db_session.refresh(sub)
    assert sub.status == "grace_period"


async def test_grace_to_suspended_marks_tenant_readonly(
    db_session: AsyncSession, make_tenant_with_plan
) -> None:
    tenant, plan = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    sub = await service.create_subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=1,
        status="grace_period",
    )
    # period_end was a long time ago (more than the 7-day grace window)
    past = utc_now() - timedelta(days=30)
    await db_session.execute(
        text("UPDATE tenant_subscription SET period_end = :ts WHERE id = :id"),
        {"ts": past, "id": sub.id},
    )
    await db_session.flush()

    moved = await service.process_grace_endings()
    assert moved >= 1
    await db_session.refresh(sub)
    assert sub.status == "suspended"

    fresh_tenant = await db_session.get(Tenant, tenant.id)
    assert fresh_tenant is not None
    assert fresh_tenant.status == "readonly"


# Mute unused-import warnings
_ = TenantSubscription
