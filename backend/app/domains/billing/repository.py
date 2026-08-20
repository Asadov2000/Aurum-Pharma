"""DB access for the billing domain.

The v_active_subscription view is read via raw text() because there's no
ORM mapping for views in this project.
"""

from __future__ import annotations

import json
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


class BillingWorkerRepository:
    """Only the two DB commands granted to the dedicated worker role."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process_trial_endings(self, *, limit: int) -> int:
        moved = await self.session.scalar(
            text("SELECT public.process_billing_trial_endings(:limit)"),
            {"limit": limit},
        )
        return int(moved or 0)

    async def process_grace_endings(self, *, limit: int) -> int:
        moved = await self.session.scalar(
            text("SELECT public.process_billing_grace_endings(:limit)"),
            {"limit": limit},
        )
        return int(moved or 0)


@dataclass(frozen=True, slots=True)
class PlatformInvoiceRecord:
    invoice: Invoice
    tenant_name: str
    subscription_status: str
    paid_amount: Decimal
    outstanding_amount: Decimal


@dataclass(frozen=True, slots=True)
class PlatformBillingTenantRecord:
    tenant_id: UUID
    name: str
    tenant_status: str
    subscription_status: str | None


@dataclass(frozen=True, slots=True)
class PlatformPricingPlanRecord:
    plan_id: UUID
    code: str
    name: str
    description: str | None
    currency: str
    is_active: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    versions: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class PlatformPricingCommandRecord:
    result: dict[str, object]
    applied: bool


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

    # -------- invoices --------

    async def get_invoice(self, invoice_id: UUID) -> Invoice | None:
        return await self.session.get(Invoice, invoice_id)

    async def list_invoices_for_tenant(self, tenant_id: UUID) -> list[Invoice]:
        stmt = (
            select(Invoice).where(Invoice.tenant_id == tenant_id).order_by(Invoice.issued_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # -------- payments --------

    async def list_payments_for_invoice(self, invoice_id: UUID) -> list[Payment]:
        stmt = (
            select(Payment).where(Payment.invoice_id == invoice_id).order_by(Payment.paid_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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

    async def list_platform_billing_tenants(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformBillingTenantRecord], int]:
        clauses = []
        term = query.strip() if query is not None else ""
        if term:
            clauses.append(Tenant.name.icontains(term, autoescape=True))
        latest_subscription_status = (
            select(TenantSubscription.status)
            .where(TenantSubscription.tenant_id == Tenant.id)
            .order_by(TenantSubscription.created_at.desc(), TenantSubscription.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        total = int(
            await self.session.scalar(select(func.count()).select_from(Tenant).where(*clauses)) or 0
        )
        rows = (
            await self.session.execute(
                select(
                    Tenant.id,
                    Tenant.name,
                    Tenant.status,
                    latest_subscription_status.label("subscription_status"),
                )
                .where(*clauses)
                .order_by(func.lower(Tenant.name), Tenant.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return (
            [
                PlatformBillingTenantRecord(
                    tenant_id=row[0],
                    name=str(row[1]),
                    tenant_status=str(row[2]),
                    subscription_status=str(row[3]) if row[3] is not None else None,
                )
                for row in rows
            ],
            total,
        )

    async def list_platform_pricing_plans(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformPricingPlanRecord], int]:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM public.list_platform_billing_plans("
                        ":actor_user_id, :actor_session_id, :limit, :offset)"
                    ),
                    {
                        "actor_user_id": actor_user_id,
                        "actor_session_id": actor_session_id,
                        "limit": page_size,
                        "offset": (page - 1) * page_size,
                    },
                )
            )
            .mappings()
            .one()
        )
        items = list(row["items"])
        return (
            [
                PlatformPricingPlanRecord(
                    plan_id=UUID(str(item["plan_id"])),
                    code=str(item["code"]),
                    name=str(item["name"]),
                    description=(
                        str(item["description"]) if item["description"] is not None else None
                    ),
                    currency=str(item["currency"]),
                    is_active=bool(item["is_active"]),
                    created_by=UUID(str(item["created_by"])),
                    created_at=datetime.fromisoformat(str(item["created_at"])),
                    updated_at=datetime.fromisoformat(str(item["updated_at"])),
                    versions=list(item["versions"]),
                )
                for item in items
            ],
            int(row["total_count"]),
        )

    async def create_platform_pricing_plan(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        code: str,
        name: str,
        description: str | None,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.create_billing_plan_draft("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":code, :name, :description)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "code": code,
                "name": name,
                "description": description,
            },
        )

    async def create_platform_pricing_price(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        plan_id: UUID,
        monthly_price_per_branch: Decimal,
        annual_discount_pct: Decimal,
        audience: str,
        notice_days: int,
        change_reason: str,
        terms_snapshot: dict[str, object],
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.create_billing_price_draft("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":plan_id, :monthly_price, :annual_discount, :audience, :notice_days, "
            ":change_reason, CAST(:terms_snapshot AS JSONB))",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "plan_id": plan_id,
                "monthly_price": monthly_price_per_branch,
                "annual_discount": annual_discount_pct,
                "audience": audience,
                "notice_days": notice_days,
                "change_reason": change_reason,
                "terms_snapshot": json.dumps(
                    terms_snapshot,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            },
        )

    async def schedule_platform_pricing_price(
        self,
        **parameters: object,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.approve_and_schedule_billing_price("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":price_version_id, :expected_row_version, :effective_from)",
            parameters,
        )

    async def activate_platform_pricing_price(
        self,
        **parameters: object,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.activate_billing_price_version("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":price_version_id, :expected_row_version)",
            parameters,
        )

    async def cancel_platform_pricing_price(
        self,
        **parameters: object,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.cancel_scheduled_billing_price("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":price_version_id, :expected_row_version, :reason_code, :reason)",
            parameters,
        )

    async def apply_initial_subscription_price(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        subscription_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.apply_initial_subscription_price("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :subscription_id, :expected_row_version)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "subscription_id": subscription_id,
                "expected_row_version": expected_row_version,
            },
        )

    async def issue_subscription_invoice(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        subscription_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.issue_billing_subscription_invoice("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :subscription_id, :expected_row_version)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "subscription_id": subscription_id,
                "expected_row_version": expected_row_version,
            },
        )

    async def create_bank_payment_review(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        target_invoice_id: UUID,
        amount: Decimal,
        paid_at: datetime,
        recipient_account_key: str,
        external_reference: str,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.create_billing_bank_payment_review("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :target_invoice_id, :amount, :paid_at, "
            ":recipient_account_key, :external_reference)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "target_invoice_id": target_invoice_id,
                "amount": amount,
                "paid_at": paid_at,
                "recipient_account_key": recipient_account_key,
                "external_reference": external_reference,
            },
        )

    async def approve_bank_payment(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        review_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.approve_billing_bank_payment("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :review_id, :expected_row_version)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "review_id": review_id,
                "expected_row_version": expected_row_version,
            },
        )

    async def reject_bank_payment_review(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        review_id: UUID,
        expected_row_version: int,
        reason_code: str,
        reason_note: str | None,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.reject_billing_bank_payment_review("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :review_id, :expected_row_version, :reason_code, :reason_note)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "review_id": review_id,
                "expected_row_version": expected_row_version,
                "reason_code": reason_code,
                "reason_note": reason_note,
            },
        )

    async def create_payment_adjustment(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        payment_id: UUID,
        adjustment_kind: str,
        amount: Decimal,
        reason_code: str,
        reason_note: str,
        refunded_at: datetime | None,
        refund_reference: str | None,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.create_billing_payment_adjustment_request("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :payment_id, :adjustment_kind, :amount, :reason_code, "
            ":reason_note, :refunded_at, :refund_reference)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "payment_id": payment_id,
                "adjustment_kind": adjustment_kind,
                "amount": amount,
                "reason_code": reason_code,
                "reason_note": reason_note,
                "refunded_at": refunded_at,
                "refund_reference": refund_reference,
            },
        )

    async def list_payment_adjustments(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        async with self.session.begin_nested():
            result = await self.session.scalar(
                text(
                    "SELECT public.list_platform_billing_payment_adjustments("
                    ":actor_user_id, :actor_session_id, :tenant_id, :limit, :offset)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "tenant_id": tenant_id,
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        return dict(result)

    async def approve_payment_adjustment(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        adjustment_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.approve_billing_payment_adjustment("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :adjustment_id, :expected_row_version)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "adjustment_id": adjustment_id,
                "expected_row_version": expected_row_version,
            },
        )

    async def reject_payment_adjustment(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        adjustment_id: UUID,
        expected_row_version: int,
        reason_code: str,
        reason_note: str | None,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.reject_billing_payment_adjustment("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :adjustment_id, :expected_row_version, :reason_code, :reason_note)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "adjustment_id": adjustment_id,
                "expected_row_version": expected_row_version,
                "reason_code": reason_code,
                "reason_note": reason_note,
            },
        )

    async def read_platform_financial_account(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
    ) -> dict[str, object]:
        async with self.session.begin_nested():
            result = await self.session.scalar(
                text(
                    "SELECT public.read_platform_billing_financial_account("
                    ":actor_user_id, :actor_session_id, :tenant_id)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "tenant_id": tenant_id,
                },
            )
        return dict(result)

    async def read_tenant_financial_account(
        self,
        *,
        actor_user_id: UUID,
        tenant_id: UUID,
    ) -> dict[str, object]:
        async with self.session.begin_nested():
            result = await self.session.scalar(
                text(
                    "SELECT public.read_tenant_billing_financial_account("
                    ":actor_user_id, :tenant_id)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "tenant_id": tenant_id,
                },
            )
        return dict(result)

    async def create_tenant_payment_submission(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        target_invoice_id: UUID,
        amount: Decimal,
        paid_at: datetime,
        external_reference: str,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.create_tenant_billing_payment_submission("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :target_invoice_id, :amount, :paid_at, :external_reference)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "target_invoice_id": target_invoice_id,
                "amount": amount,
                "paid_at": paid_at,
                "external_reference": external_reference,
            },
        )

    async def list_tenant_payment_submissions(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        async with self.session.begin_nested():
            result = await self.session.scalar(
                text(
                    "SELECT public.list_tenant_billing_payment_submissions("
                    ":actor_user_id, :actor_session_id, :tenant_id, :limit, :offset)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "tenant_id": tenant_id,
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        return dict(result)

    async def withdraw_tenant_payment_submission(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        submission_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.withdraw_tenant_billing_payment_submission("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :submission_id, :expected_row_version)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "submission_id": submission_id,
                "expected_row_version": expected_row_version,
            },
        )

    async def list_platform_payment_submissions(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        async with self.session.begin_nested():
            result = await self.session.scalar(
                text(
                    "SELECT public.list_platform_billing_payment_submissions("
                    ":actor_user_id, :actor_session_id, :tenant_id, :status, :limit, :offset)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "tenant_id": tenant_id,
                    "status": "submitted",
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        return dict(result)

    async def read_platform_payment_submission(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        submission_id: UUID,
    ) -> dict[str, object]:
        async with self.session.begin_nested():
            result = await self.session.scalar(
                text(
                    "SELECT public.read_platform_billing_payment_submission("
                    ":actor_user_id, :actor_session_id, :tenant_id, :submission_id)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "tenant_id": tenant_id,
                    "submission_id": submission_id,
                },
            )
        return dict(result)

    async def promote_payment_submission_to_review(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        submission_id: UUID,
        expected_row_version: int,
        recipient_account_key: str,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.promote_billing_payment_submission_to_review("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :submission_id, :expected_row_version, :recipient_account_key)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "submission_id": submission_id,
                "expected_row_version": expected_row_version,
                "recipient_account_key": recipient_account_key,
            },
        )

    async def reject_platform_payment_submission(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        request_hash: str,
        tenant_id: UUID,
        submission_id: UUID,
        expected_row_version: int,
        reason_code: str,
        reason_note: str | None,
    ) -> PlatformPricingCommandRecord:
        return await self._pricing_command(
            "SELECT * FROM public.reject_platform_billing_payment_submission("
            ":actor_user_id, :actor_session_id, :operation_id, :request_hash, "
            ":tenant_id, :submission_id, :expected_row_version, :reason_code, :reason_note)",
            {
                "actor_user_id": actor_user_id,
                "actor_session_id": actor_session_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "tenant_id": tenant_id,
                "submission_id": submission_id,
                "expected_row_version": expected_row_version,
                "reason_code": reason_code,
                "reason_note": reason_note,
            },
        )

    async def list_platform_payment_reviews(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        async with self.session.begin_nested():
            result = await self.session.scalar(
                text(
                    "SELECT public.list_platform_billing_payment_reviews("
                    ":actor_user_id, :actor_session_id, :tenant_id, :limit, :offset)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "tenant_id": tenant_id,
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        return dict(result)

    async def read_platform_payment_review(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        review_id: UUID,
    ) -> dict[str, object]:
        async with self.session.begin_nested():
            result = await self.session.scalar(
                text(
                    "SELECT public.read_platform_billing_payment_review("
                    ":actor_user_id, :actor_session_id, :tenant_id, :review_id)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "tenant_id": tenant_id,
                    "review_id": review_id,
                },
            )
        return dict(result)

    async def _pricing_command(
        self,
        statement: str,
        parameters: dict[str, object],
    ) -> PlatformPricingCommandRecord:
        # A rejected DB guard must not poison a longer caller-owned transaction
        # (notably the rollback-only integration-test transaction).
        async with self.session.begin_nested():
            row = (await self.session.execute(text(statement), parameters)).mappings().one()
        return PlatformPricingCommandRecord(
            result=dict(row["result"]),
            applied=bool(row["applied"]),
        )
