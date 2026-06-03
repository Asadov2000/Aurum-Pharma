"""FastAPI endpoints for the billing domain.

Two routers:
- tenant_router under /api/v1/billing/...   — read-only for tenant users
- admin_router under /api/v1/admin/tenants/... — support-only writes
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db, require_permission
from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.billing.repository import BillingRepository
from app.domains.billing.schemas import (
    InvoiceCreate,
    InvoiceRead,
    InvoiceWithPayments,
    PaymentCreate,
    PaymentRead,
    PlanRead,
    SubscriptionCreate,
    SubscriptionRead,
    SubscriptionWithPlan,
)
from app.domains.billing.service import BillingService


async def _service(db: Annotated[AsyncSession, Depends(get_db)]) -> BillingService:
    return BillingService(BillingRepository(db))


async def require_support(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    if not (user.is_developer or user.is_administrator):
        raise PermissionDeniedError("Support privileges required")
    return user


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


# =============================================================================
# Tenant-facing read-only router
# =============================================================================

tenant_router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@tenant_router.get("/plans", response_model=list[PlanRead])
async def list_plans(
    _user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[BillingService, Depends(_service)],
) -> list[PlanRead]:
    plans = await service.list_plans()
    return [PlanRead.model_validate(p) for p in plans]


@tenant_router.get("/subscription", response_model=SubscriptionWithPlan | None)
async def current_subscription(
    # reports.view (owner/admin/dev): billing/financial data, off the seller's screen.
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[BillingService, Depends(_service)],
) -> SubscriptionWithPlan | None:
    row = await service.get_active_subscription(_current_tenant_or_400(user))
    if row is None:
        return None
    return SubscriptionWithPlan.model_validate(row)


@tenant_router.get("/invoices", response_model=list[InvoiceRead])
async def list_invoices(
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[BillingService, Depends(_service)],
) -> list[InvoiceRead]:
    invoices = await service.list_invoices(_current_tenant_or_400(user))
    return [InvoiceRead.model_validate(i) for i in invoices]


@tenant_router.get("/invoices/{invoice_id}", response_model=InvoiceWithPayments)
async def get_invoice(
    invoice_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[BillingService, Depends(_service)],
) -> InvoiceWithPayments:
    inv, payments = await service.get_invoice_with_payments(invoice_id)
    return InvoiceWithPayments(
        **InvoiceRead.model_validate(inv).model_dump(),
        payments=[PaymentRead.model_validate(p) for p in payments],
    )


# =============================================================================
# Admin (support-only) router
# =============================================================================

admin_router = APIRouter(prefix="/api/v1/admin/tenants", tags=["admin"])


@admin_router.post(
    "/{tenant_id}/subscription",
    response_model=SubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_support)],
)
async def create_subscription(
    tenant_id: UUID,
    payload: SubscriptionCreate,
    service: Annotated[BillingService, Depends(_service)],
) -> SubscriptionRead:
    sub = await service.create_subscription(
        tenant_id=tenant_id,
        plan_id=payload.plan_id,
        billing_period=payload.billing_period,
        branches_count=payload.branches_count,
        status="active",
    )
    return SubscriptionRead.model_validate(sub)


@admin_router.post(
    "/{tenant_id}/invoices",
    response_model=InvoiceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_support)],
)
async def create_invoice(
    tenant_id: UUID,
    payload: InvoiceCreate,
    service: Annotated[BillingService, Depends(_service)],
) -> InvoiceRead:
    inv = await service.create_invoice(
        tenant_id=tenant_id,
        subscription_id=payload.subscription_id,
        amount=payload.amount,
        due_in_days=payload.due_in_days,
        notes=payload.notes,
        discount_amount=payload.discount_amount,
        discount_reason=payload.discount_reason,
    )
    return InvoiceRead.model_validate(inv)


@admin_router.post(
    "/{tenant_id}/invoices/{invoice_id}/payments",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_payment(
    tenant_id: UUID,
    invoice_id: UUID,
    payload: PaymentCreate,
    user: Annotated[CurrentUser, Depends(require_support)],
    service: Annotated[BillingService, Depends(_service)],
) -> PaymentRead:
    payment = await service.record_payment(
        tenant_id=tenant_id,
        invoice_id=invoice_id,
        amount=payload.amount,
        paid_at=payload.paid_at,
        method=payload.method,
        reference=payload.reference,
        notes=payload.notes,
        recorded_by=user.user_id,
    )
    return PaymentRead.model_validate(payment)
