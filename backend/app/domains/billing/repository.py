"""DB access for the billing domain.

The v_active_subscription view is read via raw text() because there's no
ORM mapping for views in this project.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.billing.models import (
    Invoice,
    Payment,
    SubscriptionPlan,
    TenantSubscription,
)
from app.domains.foundation.models import Tenant


@dataclass(frozen=True, slots=True)
class PlatformBillingOverview:
    tenants_total: int
    active_subscriptions: int
    attention_subscriptions: int
    open_invoices: int
    overdue_invoices: int
    outstanding_amount: Decimal


@dataclass(frozen=True, slots=True)
class PlatformInvoiceRecord:
    invoice: Invoice
    tenant_name: str
    subscription_status: str
    paid_amount: Decimal
    outstanding_amount: Decimal


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

    async def sum_payments(self, invoice_id: UUID) -> Decimal:
        stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.invoice_id == invoice_id
        )
        result = await self.session.execute(stmt)
        return Decimal(result.scalar_one()).quantize(Decimal("0.01"))

    # -------- platform read model --------

    async def get_platform_overview(self, *, now: datetime) -> PlatformBillingOverview:
        payment_totals = (
            select(
                Payment.tenant_id.label("tenant_id"),
                Payment.invoice_id.label("invoice_id"),
                func.coalesce(func.sum(Payment.amount), 0).label("paid_amount"),
            )
            .group_by(Payment.tenant_id, Payment.invoice_id)
            .subquery()
        )
        paid_amount = func.coalesce(payment_totals.c.paid_amount, 0)
        outstanding = func.greatest(Invoice.amount - paid_amount, 0)
        open_clause = and_(Invoice.status.in_(("pending", "overdue")), outstanding > 0)

        tenant_count = await self.session.scalar(select(func.count()).select_from(Tenant))
        active_count = await self.session.scalar(
            select(func.count())
            .select_from(TenantSubscription)
            .where(TenantSubscription.status.in_(("trial", "active")))
        )
        attention_count = await self.session.scalar(
            select(func.count())
            .select_from(TenantSubscription)
            .where(TenantSubscription.status.in_(("grace_period", "suspended")))
        )
        invoice_row = (
            await self.session.execute(
                select(
                    func.count(Invoice.id).filter(open_clause),
                    func.count(Invoice.id).filter(and_(open_clause, Invoice.due_at < now)),
                    func.coalesce(func.sum(outstanding).filter(open_clause), 0),
                )
                .select_from(Invoice)
                .outerjoin(
                    payment_totals,
                    and_(
                        payment_totals.c.tenant_id == Invoice.tenant_id,
                        payment_totals.c.invoice_id == Invoice.id,
                    ),
                )
            )
        ).one()
        return PlatformBillingOverview(
            tenants_total=int(tenant_count or 0),
            active_subscriptions=int(active_count or 0),
            attention_subscriptions=int(attention_count or 0),
            open_invoices=int(invoice_row[0] or 0),
            overdue_invoices=int(invoice_row[1] or 0),
            outstanding_amount=Decimal(invoice_row[2] or 0).quantize(Decimal("0.01")),
        )

    async def list_platform_invoices(
        self,
        *,
        now: datetime,
        query: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformInvoiceRecord], int]:
        payment_totals = (
            select(
                Payment.tenant_id.label("tenant_id"),
                Payment.invoice_id.label("invoice_id"),
                func.coalesce(func.sum(Payment.amount), 0).label("paid_amount"),
            )
            .group_by(Payment.tenant_id, Payment.invoice_id)
            .subquery()
        )
        paid_amount = func.coalesce(payment_totals.c.paid_amount, 0)
        outstanding = func.greatest(Invoice.amount - paid_amount, 0)
        clauses = []
        term = query.strip() if query is not None else ""
        if term:
            clauses.append(
                or_(
                    Invoice.invoice_number.icontains(term, autoescape=True),
                    Tenant.name.icontains(term, autoescape=True),
                )
            )
        if status == "overdue":
            clauses.append(
                and_(
                    Invoice.status.in_(("pending", "overdue")),
                    outstanding > 0,
                    Invoice.due_at < now,
                )
            )
        elif status == "pending":
            clauses.append(
                and_(
                    Invoice.status == "pending",
                    outstanding > 0,
                    Invoice.due_at >= now,
                )
            )
        elif status is not None:
            clauses.append(Invoice.status == status)

        joins = (
            Invoice.__table__.join(Tenant.__table__, Tenant.id == Invoice.tenant_id)
            .join(
                TenantSubscription.__table__,
                and_(
                    TenantSubscription.id == Invoice.subscription_id,
                    TenantSubscription.tenant_id == Invoice.tenant_id,
                ),
            )
            .outerjoin(
                payment_totals,
                and_(
                    payment_totals.c.tenant_id == Invoice.tenant_id,
                    payment_totals.c.invoice_id == Invoice.id,
                ),
            )
        )
        total = int(
            await self.session.scalar(select(func.count()).select_from(joins).where(*clauses)) or 0
        )
        priority = case(
            (and_(outstanding > 0, Invoice.due_at < now), 0),
            (outstanding > 0, 1),
            else_=2,
        )
        stmt = (
            select(
                Invoice,
                Tenant.name,
                TenantSubscription.status,
                paid_amount.label("paid_amount"),
                outstanding.label("outstanding_amount"),
            )
            .select_from(joins)
            .where(*clauses)
            .order_by(priority, Invoice.due_at.asc(), Invoice.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).all()
        return (
            [
                PlatformInvoiceRecord(
                    invoice=row[0],
                    tenant_name=str(row[1]),
                    subscription_status=str(row[2]),
                    paid_amount=Decimal(row[3] or 0).quantize(Decimal("0.01")),
                    outstanding_amount=Decimal(row[4] or 0).quantize(Decimal("0.01")),
                )
                for row in rows
            ],
            total,
        )
