"""DB access for the billing domain.

The v_active_subscription view is read via raw text() because there's no
ORM mapping for views in this project.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.billing.models import (
    Invoice,
    Payment,
    SubscriptionPlan,
    TenantSubscription,
)


class BillingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------- plans --------

    async def list_plans(self) -> list[SubscriptionPlan]:
        stmt = (
            select(SubscriptionPlan)
            .where(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.code)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_plan(self, plan_id: UUID) -> SubscriptionPlan | None:
        return await self.session.get(SubscriptionPlan, plan_id)

    async def get_plan_by_code(self, code: str) -> SubscriptionPlan | None:
        stmt = select(SubscriptionPlan).where(SubscriptionPlan.code == code).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # -------- subscriptions --------

    async def insert_subscription(self, **fields: Any) -> TenantSubscription:
        sub = TenantSubscription(**fields)
        self.session.add(sub)
        await self.session.flush()
        await self.session.refresh(sub)
        return sub

    async def update_subscription(
        self, sub: TenantSubscription, **fields: Any
    ) -> TenantSubscription:
        for k, v in fields.items():
            setattr(sub, k, v)
        await self.session.flush()
        await self.session.refresh(sub)
        return sub

    async def get_subscription(self, sub_id: UUID) -> TenantSubscription | None:
        return await self.session.get(TenantSubscription, sub_id)

    async def get_active_subscription(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Returns a row from v_active_subscription as a plain dict so the
        router can build SubscriptionWithPlan."""
        stmt = text("SELECT * FROM v_active_subscription WHERE tenant_id = :tid LIMIT 1")
        result = await self.session.execute(stmt, {"tid": tenant_id})
        row = result.first()
        return dict(row._mapping) if row is not None else None

    async def list_subscriptions_with_period_end_before(
        self,
        *,
        status: str,
        cutoff: datetime,
    ) -> list[TenantSubscription]:
        stmt = select(TenantSubscription).where(
            and_(
                TenantSubscription.status == status,
                TenantSubscription.period_end < cutoff,
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # -------- invoices --------

    async def next_invoice_number(self) -> str:
        """Sequential global counter. Total rows + 1, padded to 8 digits."""
        stmt = select(func.count()).select_from(Invoice)
        result = await self.session.execute(stmt)
        n = int(result.scalar_one()) + 1
        return f"INV-{n:08d}"

    async def insert_invoice(self, **fields: Any) -> Invoice:
        inv = Invoice(**fields)
        self.session.add(inv)
        await self.session.flush()
        await self.session.refresh(inv)
        return inv

    async def get_invoice(self, invoice_id: UUID) -> Invoice | None:
        return await self.session.get(Invoice, invoice_id)

    async def list_invoices_for_tenant(self, tenant_id: UUID) -> list[Invoice]:
        stmt = (
            select(Invoice).where(Invoice.tenant_id == tenant_id).order_by(Invoice.issued_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_invoice(self, inv: Invoice, **fields: Any) -> Invoice:
        for k, v in fields.items():
            setattr(inv, k, v)
        await self.session.flush()
        await self.session.refresh(inv)
        return inv

    async def invoice_exists_for_subscription(self, subscription_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(Invoice)
            .where(Invoice.subscription_id == subscription_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) > 0

    # -------- payments --------

    async def insert_payment(self, **fields: Any) -> Payment:
        p = Payment(**fields)
        self.session.add(p)
        await self.session.flush()
        await self.session.refresh(p)
        return p

    async def list_payments_for_invoice(self, invoice_id: UUID) -> list[Payment]:
        stmt = (
            select(Payment).where(Payment.invoice_id == invoice_id).order_by(Payment.paid_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def sum_payments(self, invoice_id: UUID) -> float:
        stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.invoice_id == invoice_id
        )
        result = await self.session.execute(stmt)
        return float(result.scalar_one())
