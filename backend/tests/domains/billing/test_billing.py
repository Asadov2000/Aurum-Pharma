"""Billing lifecycle — subscription, invoice, payment, transitions."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.core.time import utc_now
from app.domains.billing.models import TenantSubscription
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService
from app.domains.foundation.models import Branch, Tenant


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


async def test_create_invoice_for_subscription(
    db_session: AsyncSession, make_tenant_with_plan
) -> None:
    tenant, plan = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    sub = await service.create_subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=2,
        status="active",
    )
    inv = await service.create_invoice(
        tenant_id=tenant.id,
        subscription_id=sub.id,
        amount=sub.amount,
    )
    assert inv.status == "pending"
    assert inv.amount == sub.amount
    assert inv.invoice_number.startswith("INV-")


async def test_payment_marks_invoice_as_paid(
    db_session: AsyncSession, make_tenant_with_plan
) -> None:
    tenant, plan = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    sub = await service.create_subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=1,
        status="active",
    )
    inv = await service.create_invoice(
        tenant_id=tenant.id, subscription_id=sub.id, amount=sub.amount
    )

    await service.record_payment(
        tenant_id=tenant.id,
        invoice_id=inv.id,
        amount=sub.amount,
        paid_at=utc_now(),
        method="bank_transfer",
        reference="PAY-001",
        notes=None,
        recorded_by=None,
    )
    refreshed = await service.get_invoice(inv.id)
    assert refreshed.status == "paid"
    assert refreshed.paid_at is not None


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


async def test_branches_change_triggers_recalc(
    db_session: AsyncSession, make_tenant_with_plan
) -> None:
    from app.domains.foundation.repository import FoundationRepository as FRepo
    from app.domains.foundation.service import FoundationService

    tenant, plan = await make_tenant_with_plan()
    billing = BillingService(BillingRepository(db_session))
    foundation = FoundationService(FRepo(db_session))

    await billing.create_subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=1,
        status="active",
    )
    # Create two branches and recalc
    await foundation.create_branch(tenant_id=tenant.id, fields={"name": "A"})
    await foundation.create_branch(tenant_id=tenant.id, fields={"name": "B"})

    await billing.recalculate_on_branch_change(tenant.id)

    row = await billing.get_active_subscription(tenant.id)
    assert row is not None
    assert row["branches_count"] == 2
    # Amount should be price_per_branch * 2
    expected = plan.price_per_branch * 2
    assert Decimal(str(row["amount"])) == expected.quantize(Decimal("0.01"))


async def test_payment_overpay_rejected(db_session: AsyncSession, make_tenant_with_plan) -> None:
    tenant, plan = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    sub = await service.create_subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=1,
        status="active",
    )
    inv = await service.create_invoice(
        tenant_id=tenant.id, subscription_id=sub.id, amount=sub.amount
    )

    # First payment of full amount → invoice paid
    await service.record_payment(
        tenant_id=tenant.id,
        invoice_id=inv.id,
        amount=sub.amount,
        paid_at=utc_now(),
        method="bank_transfer",
        reference="r1",
        notes=None,
        recorded_by=None,
    )
    # Second payment refused because invoice is now 'paid'
    with pytest.raises(BusinessRuleError):
        await service.record_payment(
            tenant_id=tenant.id,
            invoice_id=inv.id,
            amount=Decimal("1.00"),
            paid_at=utc_now(),
            method="bank_transfer",
            reference="r2",
            notes=None,
            recorded_by=None,
        )


# Mute unused-import warnings
_ = (TenantSubscription, Branch, select)
