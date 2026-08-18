"""FastAPI endpoints for the billing domain.

Two routers:
- tenant_router under /api/v1/billing/...   — read-only for tenant users
- admin_router under /api/v1/admin/tenants/... — support-only writes
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    current_user,
    get_db,
    require_recent_platform_capability,
    require_tenant_permission,
)
from app.core.errors import BusinessRuleError
from app.core.time import utc_now
from app.domains.billing.repository import BillingRepository
from app.domains.billing.schemas import (
    BankPaymentApprovalDetail,
    BankPaymentApprovalQueue,
    BankPaymentApprove,
    BankPaymentReviewCommandResult,
    BankPaymentReviewCreate,
    BankPaymentReviewRead,
    BankPaymentReviewReject,
    BillingFinancialAccountRead,
    BillingFinancialInvoiceRead,
    BillingInvoiceCommandResult,
    BillingInvoiceIssue,
    BillingPaymentAdjustmentApprovalCommandResult,
    BillingPaymentAdjustmentApprovalRead,
    BillingPaymentAdjustmentApprove,
    BillingPaymentAdjustmentCreate,
    BillingPaymentAdjustmentQueue,
    BillingPaymentAdjustmentReject,
    BillingPaymentAdjustmentRejectionCommandResult,
    BillingPaymentAdjustmentRejectionRead,
    BillingPaymentAdjustmentRequestCommandResult,
    BillingPaymentAdjustmentRequestRead,
    BillingPaymentApprovalCommandResult,
    BillingPaymentApprovalRead,
    InvoiceCreate,
    InvoiceRead,
    InvoiceWithPayments,
    PaymentCreate,
    PaymentRead,
    PaymentSubmissionCommandResult,
    PaymentSubmissionCreate,
    PaymentSubmissionList,
    PaymentSubmissionRead,
    PaymentSubmissionWithdraw,
    PlanRead,
    PlatformBillingOverviewRead,
    PlatformBillingTenantList,
    PlatformBillingTenantRead,
    PlatformInvoiceList,
    PlatformInvoiceRead,
    PlatformPaymentSubmissionDetail,
    PlatformPaymentSubmissionQueue,
    PlatformPaymentSubmissionReject,
    PlatformPaymentSubmissionReview,
    PlatformPricingPlanCommandResult,
    PlatformPricingPlanList,
    PlatformPricingPlanRead,
    PlatformPricingVersionCommandResult,
    PlatformPricingVersionRead,
    PricingActivate,
    PricingCancel,
    PricingPlanCreate,
    PricingPriceDraftCreate,
    PricingSchedule,
    SubscriptionCreate,
    SubscriptionPriceApplicationCommandResult,
    SubscriptionPriceApplicationCreate,
    SubscriptionPriceApplicationRead,
    SubscriptionRead,
    SubscriptionWithPlan,
    TenantBillingFinancialAccountRead,
)
from app.domains.billing.service import BillingService


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> BillingService:
    return BillingService(BillingRepository(db))


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


def _tenant_session_or_401(user: CurrentUser) -> UUID:
    if user.session_id is None:
        raise BusinessRuleError("Billing operation requires an authentication session")
    return user.session_id


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
    user: Annotated[
        CurrentUser,
        Depends(require_tenant_permission("reports.view")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> SubscriptionWithPlan | None:
    row = await service.get_active_subscription(_current_tenant_or_400(user))
    if row is None:
        return None
    return SubscriptionWithPlan.model_validate(row)


@tenant_router.get("/invoices", response_model=list[InvoiceRead])
async def list_invoices(
    user: Annotated[
        CurrentUser,
        Depends(require_tenant_permission("reports.view")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> list[InvoiceRead]:
    invoices = await service.list_invoices(_current_tenant_or_400(user))
    return [InvoiceRead.model_validate(i) for i in invoices]


@tenant_router.get(
    "/financial-account",
    response_model=TenantBillingFinancialAccountRead,
    dependencies=[Depends(require_tenant_permission("billing.overview.view"))],
)
async def read_tenant_financial_account(
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_tenant_permission("billing.invoice.view")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> TenantBillingFinancialAccountRead:
    _set_financial_no_store(response)
    account = await service.read_tenant_financial_account(
        actor_user_id=user.user_id,
        tenant_id=_current_tenant_or_400(user),
    )
    return TenantBillingFinancialAccountRead.model_validate(account)


@tenant_router.get(
    "/payment-submissions",
    response_model=PaymentSubmissionList,
)
async def list_tenant_payment_submissions(
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_tenant_permission("billing.invoice.view")),
    ],
    service: Annotated[BillingService, Depends(_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaymentSubmissionList:
    _set_financial_no_store(response)
    result = await service.list_tenant_payment_submissions(
        actor_user_id=user.user_id,
        actor_session_id=_tenant_session_or_401(user),
        tenant_id=_current_tenant_or_400(user),
        page=page,
        page_size=page_size,
    )
    return PaymentSubmissionList.model_validate({**result, "page": page, "page_size": page_size})


@tenant_router.post(
    "/payment-submissions",
    response_model=PaymentSubmissionCommandResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_payment_submission(
    payload: PaymentSubmissionCreate,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_tenant_permission("billing.payment_submission.create")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PaymentSubmissionCommandResult:
    _set_financial_no_store(response)
    record = await service.create_tenant_payment_submission(
        actor_user_id=user.user_id,
        actor_session_id=_tenant_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=_current_tenant_or_400(user),
        target_invoice_id=payload.target_invoice_id,
        amount=payload.amount,
        paid_at=payload.paid_at,
        external_reference=payload.external_reference,
    )
    return PaymentSubmissionCommandResult(
        item=PaymentSubmissionRead.model_validate(record.result),
        applied=record.applied,
    )


@tenant_router.post(
    "/payment-submissions/{submission_id}/withdraw",
    response_model=PaymentSubmissionCommandResult,
)
async def withdraw_tenant_payment_submission(
    submission_id: UUID,
    payload: PaymentSubmissionWithdraw,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_tenant_permission("billing.payment_submission.withdraw")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PaymentSubmissionCommandResult:
    _set_financial_no_store(response)
    record = await service.withdraw_tenant_payment_submission(
        actor_user_id=user.user_id,
        actor_session_id=_tenant_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=_current_tenant_or_400(user),
        submission_id=submission_id,
        expected_row_version=payload.expected_row_version,
    )
    return PaymentSubmissionCommandResult(
        item=PaymentSubmissionRead.model_validate(record.result),
        applied=record.applied,
    )


@tenant_router.get("/invoices/{invoice_id}", response_model=InvoiceWithPayments)
async def get_invoice(
    invoice_id: UUID,
    _user: Annotated[
        CurrentUser,
        Depends(require_tenant_permission("reports.view")),
    ],
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
platform_router = APIRouter(prefix="/api/v1/admin/billing", tags=["admin", "billing"])


def _set_financial_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def _platform_session_or_401(user: CurrentUser) -> UUID:
    if user.session_id is None:
        raise BusinessRuleError("Platform operation requires an authentication session")
    return user.session_id


@platform_router.get(
    "/plans",
    response_model=PlatformPricingPlanList,
)
async def list_platform_pricing_plans(
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.view")),
    ],
    service: Annotated[BillingService, Depends(_service)],
    page: int = Query(default=1, ge=1, le=1000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlatformPricingPlanList:
    _set_financial_no_store(response)
    records, total = await service.list_platform_pricing_plans(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        page=page,
        page_size=page_size,
    )
    return PlatformPricingPlanList(
        items=[
            PlatformPricingPlanRead(
                plan_id=record.plan_id,
                code=record.code,
                name=record.name,
                description=record.description,
                currency=record.currency,
                is_active=record.is_active,
                created_by=record.created_by,
                created_at=record.created_at,
                updated_at=record.updated_at,
                versions=[
                    PlatformPricingVersionRead.model_validate(item) for item in record.versions
                ],
            )
            for record in records
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@platform_router.post(
    "/plans",
    response_model=PlatformPricingPlanCommandResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform_pricing_plan(
    payload: PricingPlanCreate,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.plan.manage")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PlatformPricingPlanCommandResult:
    _set_financial_no_store(response)
    record = await service.create_platform_pricing_plan(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
    )
    return PlatformPricingPlanCommandResult(
        item=PlatformPricingPlanRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.post(
    "/plans/{plan_id}/prices",
    response_model=PlatformPricingVersionCommandResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform_pricing_price(
    plan_id: UUID,
    payload: PricingPriceDraftCreate,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.plan.manage")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PlatformPricingVersionCommandResult:
    _set_financial_no_store(response)
    record = await service.create_platform_pricing_price(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        plan_id=plan_id,
        monthly_price_per_branch=payload.monthly_price_per_branch,
        annual_discount_pct=payload.annual_discount_pct,
        audience=payload.audience,
        notice_days=payload.notice_days,
        change_reason=payload.change_reason,
        terms_snapshot=dict(payload.terms_snapshot),
    )
    return PlatformPricingVersionCommandResult(
        item=PlatformPricingVersionRead.model_validate(record.result),
        applied=record.applied,
    )


async def _transition_platform_price(
    *,
    action: str,
    price_id: UUID,
    operation_id: UUID,
    expected_row_version: int,
    response: Response,
    user: CurrentUser,
    service: BillingService,
    effective_from: datetime | None = None,
    reason_code: str | None = None,
    reason: str | None = None,
) -> PlatformPricingVersionCommandResult:
    _set_financial_no_store(response)
    record = await service.transition_platform_pricing_price(
        action=action,
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=operation_id,
        price_version_id=price_id,
        expected_row_version=expected_row_version,
        effective_from=effective_from,
        reason_code=reason_code,
        reason=reason,
    )
    return PlatformPricingVersionCommandResult(
        item=PlatformPricingVersionRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.post(
    "/prices/{price_id}/schedule",
    response_model=PlatformPricingVersionCommandResult,
)
async def schedule_platform_pricing_price(
    price_id: UUID,
    payload: PricingSchedule,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.plan.manage")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PlatformPricingVersionCommandResult:
    return await _transition_platform_price(
        action="price_scheduled",
        price_id=price_id,
        operation_id=payload.operation_id,
        expected_row_version=payload.expected_row_version,
        effective_from=payload.effective_from,
        response=response,
        user=user,
        service=service,
    )


@platform_router.post(
    "/prices/{price_id}/activate",
    response_model=PlatformPricingVersionCommandResult,
)
async def activate_platform_pricing_price(
    price_id: UUID,
    payload: PricingActivate,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.plan.manage")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PlatformPricingVersionCommandResult:
    return await _transition_platform_price(
        action="price_activated",
        price_id=price_id,
        operation_id=payload.operation_id,
        expected_row_version=payload.expected_row_version,
        response=response,
        user=user,
        service=service,
    )


@platform_router.post(
    "/prices/{price_id}/cancel",
    response_model=PlatformPricingVersionCommandResult,
)
async def cancel_platform_pricing_price(
    price_id: UUID,
    payload: PricingCancel,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.plan.manage")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PlatformPricingVersionCommandResult:
    return await _transition_platform_price(
        action="price_cancelled",
        price_id=price_id,
        operation_id=payload.operation_id,
        expected_row_version=payload.expected_row_version,
        reason_code=payload.reason_code,
        reason=payload.reason,
        response=response,
        user=user,
        service=service,
    )


@platform_router.get(
    "/overview",
    response_model=PlatformBillingOverviewRead,
    dependencies=[Depends(require_recent_platform_capability("platform.billing.view"))],
)
async def get_platform_billing_overview(
    response: Response,
    service: Annotated[BillingService, Depends(_service)],
) -> PlatformBillingOverviewRead:
    _set_financial_no_store(response)
    overview = await service.get_platform_overview()
    return PlatformBillingOverviewRead(
        generated_at=utc_now(),
        tenants_total=overview.tenants_total,
        active_subscriptions=overview.active_subscriptions,
        attention_subscriptions=overview.attention_subscriptions,
        open_invoices=overview.open_invoices,
        overdue_invoices=overview.overdue_invoices,
        outstanding_amount=overview.outstanding_amount,
    )


@platform_router.get(
    "/tenants",
    response_model=PlatformBillingTenantList,
    dependencies=[Depends(require_recent_platform_capability("platform.billing.view"))],
)
async def list_platform_billing_tenants(
    response: Response,
    service: Annotated[BillingService, Depends(_service)],
    q: str | None = Query(default=None, min_length=1, max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlatformBillingTenantList:
    _set_financial_no_store(response)
    records, total = await service.list_platform_billing_tenants(
        query=q,
        page=page,
        page_size=page_size,
    )
    return PlatformBillingTenantList(
        items=[
            PlatformBillingTenantRead(
                tenant_id=record.tenant_id,
                name=record.name,
                tenant_status=record.tenant_status,
                subscription_status=record.subscription_status,
            )
            for record in records
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@platform_router.get(
    "/invoices",
    response_model=PlatformInvoiceList,
    dependencies=[Depends(require_recent_platform_capability("platform.billing.view"))],
)
async def list_platform_billing_invoices(
    response: Response,
    service: Annotated[BillingService, Depends(_service)],
    q: str | None = Query(default=None, min_length=1, max_length=120),
    invoice_status: Literal["pending", "overdue", "paid", "cancelled"] | None = Query(
        default=None,
        alias="status",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlatformInvoiceList:
    _set_financial_no_store(response)
    records, total = await service.list_platform_invoices(
        query=q,
        status=invoice_status,
        page=page,
        page_size=page_size,
    )
    now = utc_now()
    items: list[PlatformInvoiceRead] = []
    for record in records:
        invoice_status_value = record.invoice.status
        if (
            invoice_status_value in ("pending", "overdue")
            and record.outstanding_amount > 0
            and record.invoice.due_at < now
        ):
            invoice_status_value = "overdue"
        items.append(
            PlatformInvoiceRead(
                tenant_name=record.tenant_name,
                invoice_number=record.invoice.invoice_number,
                issued_at=record.invoice.issued_at,
                due_at=record.invoice.due_at,
                amount=record.invoice.amount,
                paid_amount=record.paid_amount,
                outstanding_amount=record.outstanding_amount,
                currency=record.invoice.currency,
                status=invoice_status_value,
                subscription_status=record.subscription_status,
            )
        )
    return PlatformInvoiceList(items=items, total=total, page=page, page_size=page_size)


@admin_router.post(
    "/{tenant_id}/subscription",
    response_model=SubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_recent_platform_capability("platform.billing.manage"))],
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
    "/{tenant_id}/subscriptions/{subscription_id}/price-applications/initial",
    response_model=SubscriptionPriceApplicationCommandResult,
    status_code=status.HTTP_201_CREATED,
)
async def apply_initial_subscription_price(
    tenant_id: UUID,
    subscription_id: UUID,
    payload: SubscriptionPriceApplicationCreate,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.plan.manage")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> SubscriptionPriceApplicationCommandResult:
    _set_financial_no_store(response)
    record = await service.apply_initial_subscription_price(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        expected_row_version=payload.expected_row_version,
    )
    return SubscriptionPriceApplicationCommandResult(
        item=SubscriptionPriceApplicationRead.model_validate(record.result),
        applied=record.applied,
    )


@admin_router.post(
    "/{tenant_id}/subscriptions/{subscription_id}/financial-invoices",
    response_model=BillingInvoiceCommandResult,
    status_code=status.HTTP_201_CREATED,
)
async def issue_subscription_invoice(
    tenant_id: UUID,
    subscription_id: UUID,
    payload: BillingInvoiceIssue,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.invoice.issue")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BillingInvoiceCommandResult:
    _set_financial_no_store(response)
    record = await service.issue_subscription_invoice(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        expected_row_version=payload.expected_row_version,
    )
    return BillingInvoiceCommandResult(
        item=BillingFinancialInvoiceRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.get(
    "/tenants/{tenant_id}/payment-submissions",
    response_model=PlatformPaymentSubmissionQueue,
)
async def list_platform_payment_submissions(
    tenant_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.review")),
    ],
    service: Annotated[BillingService, Depends(_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlatformPaymentSubmissionQueue:
    _set_financial_no_store(response)
    queue = await service.list_platform_payment_submissions(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
    )
    return PlatformPaymentSubmissionQueue.model_validate(
        {**queue, "page": page, "page_size": page_size}
    )


@platform_router.get(
    "/tenants/{tenant_id}/payment-submissions/{submission_id}",
    response_model=PlatformPaymentSubmissionDetail,
)
async def read_platform_payment_submission(
    tenant_id: UUID,
    submission_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.review")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PlatformPaymentSubmissionDetail:
    _set_financial_no_store(response)
    item = await service.read_platform_payment_submission(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        tenant_id=tenant_id,
        submission_id=submission_id,
    )
    return PlatformPaymentSubmissionDetail.model_validate(item)


@platform_router.post(
    "/tenants/{tenant_id}/payment-submissions/{submission_id}/review",
    response_model=BankPaymentReviewCommandResult,
    status_code=status.HTTP_201_CREATED,
)
async def promote_payment_submission_to_review(
    tenant_id: UUID,
    submission_id: UUID,
    payload: PlatformPaymentSubmissionReview,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.review")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BankPaymentReviewCommandResult:
    _set_financial_no_store(response)
    record = await service.promote_payment_submission_to_review(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        submission_id=submission_id,
        expected_row_version=payload.expected_row_version,
        recipient_account_key=payload.recipient_account_key,
    )
    return BankPaymentReviewCommandResult(
        item=BankPaymentReviewRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.post(
    "/tenants/{tenant_id}/payment-submissions/{submission_id}/reject",
    response_model=PaymentSubmissionCommandResult,
)
async def reject_platform_payment_submission(
    tenant_id: UUID,
    submission_id: UUID,
    payload: PlatformPaymentSubmissionReject,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.review")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> PaymentSubmissionCommandResult:
    _set_financial_no_store(response)
    record = await service.reject_platform_payment_submission(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        submission_id=submission_id,
        expected_row_version=payload.expected_row_version,
        reason_code=payload.reason_code,
        reason_note=payload.reason_note,
    )
    return PaymentSubmissionCommandResult(
        item=PaymentSubmissionRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.post(
    "/tenants/{tenant_id}/payment-reviews",
    response_model=BankPaymentReviewCommandResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_bank_payment_review(
    tenant_id: UUID,
    payload: BankPaymentReviewCreate,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.review")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BankPaymentReviewCommandResult:
    _set_financial_no_store(response)
    record = await service.create_bank_payment_review(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        target_invoice_id=payload.target_invoice_id,
        amount=payload.amount,
        paid_at=payload.paid_at,
        recipient_account_key=payload.recipient_account_key,
        external_reference=payload.external_reference,
    )
    return BankPaymentReviewCommandResult(
        item=BankPaymentReviewRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.get(
    "/tenants/{tenant_id}/payment-reviews",
    response_model=BankPaymentApprovalQueue,
)
async def list_bank_payment_reviews(
    tenant_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.approve")),
    ],
    service: Annotated[BillingService, Depends(_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> BankPaymentApprovalQueue:
    _set_financial_no_store(response)
    queue = await service.list_platform_payment_reviews(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
    )
    return BankPaymentApprovalQueue.model_validate({**queue, "page": page, "page_size": page_size})


@platform_router.get(
    "/tenants/{tenant_id}/payment-reviews/{review_id}",
    response_model=BankPaymentApprovalDetail,
)
async def read_bank_payment_review(
    tenant_id: UUID,
    review_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.approve")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BankPaymentApprovalDetail:
    _set_financial_no_store(response)
    item = await service.read_platform_payment_review(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        tenant_id=tenant_id,
        review_id=review_id,
    )
    return BankPaymentApprovalDetail.model_validate(item)


@platform_router.get(
    "/tenants/{tenant_id}/financial-account",
    response_model=BillingFinancialAccountRead,
)
async def read_platform_financial_account(
    tenant_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.view")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BillingFinancialAccountRead:
    _set_financial_no_store(response)
    account = await service.read_platform_financial_account(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        tenant_id=tenant_id,
    )
    return BillingFinancialAccountRead.model_validate(account)


@platform_router.post(
    "/tenants/{tenant_id}/payment-reviews/{review_id}/approve",
    response_model=BillingPaymentApprovalCommandResult,
)
async def approve_bank_payment(
    tenant_id: UUID,
    review_id: UUID,
    payload: BankPaymentApprove,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.approve")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BillingPaymentApprovalCommandResult:
    _set_financial_no_store(response)
    record = await service.approve_bank_payment(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        review_id=review_id,
        expected_row_version=payload.expected_row_version,
    )
    return BillingPaymentApprovalCommandResult(
        item=BillingPaymentApprovalRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.post(
    "/tenants/{tenant_id}/payment-reviews/{review_id}/reject",
    response_model=BankPaymentReviewCommandResult,
)
async def reject_bank_payment_review(
    tenant_id: UUID,
    review_id: UUID,
    payload: BankPaymentReviewReject,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.payment.approve")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BankPaymentReviewCommandResult:
    _set_financial_no_store(response)
    record = await service.reject_bank_payment_review(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        review_id=review_id,
        expected_row_version=payload.expected_row_version,
        reason_code=payload.reason_code,
        reason_note=payload.reason_note,
    )
    return BankPaymentReviewCommandResult(
        item=BankPaymentReviewRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.post(
    "/tenants/{tenant_id}/payments/{payment_id}/adjustments",
    response_model=BillingPaymentAdjustmentRequestCommandResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_adjustment(
    tenant_id: UUID,
    payment_id: UUID,
    payload: BillingPaymentAdjustmentCreate,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.adjustment.create")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BillingPaymentAdjustmentRequestCommandResult:
    _set_financial_no_store(response)
    record = await service.create_payment_adjustment(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        payment_id=payment_id,
        adjustment_kind=payload.adjustment_kind,
        amount=payload.amount,
        reason_code=payload.reason_code,
        reason_note=payload.reason_note,
        refunded_at=payload.refunded_at,
        refund_reference=payload.refund_reference,
    )
    return BillingPaymentAdjustmentRequestCommandResult(
        item=BillingPaymentAdjustmentRequestRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.get(
    "/tenants/{tenant_id}/payment-adjustments",
    response_model=BillingPaymentAdjustmentQueue,
)
async def list_payment_adjustments(
    tenant_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.adjustment.approve")),
    ],
    service: Annotated[BillingService, Depends(_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> BillingPaymentAdjustmentQueue:
    _set_financial_no_store(response)
    queue = await service.list_payment_adjustments(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
    )
    return BillingPaymentAdjustmentQueue.model_validate(
        {**queue, "page": page, "page_size": page_size}
    )


@platform_router.post(
    "/tenants/{tenant_id}/payment-adjustments/{adjustment_id}/approve",
    response_model=BillingPaymentAdjustmentApprovalCommandResult,
)
async def approve_payment_adjustment(
    tenant_id: UUID,
    adjustment_id: UUID,
    payload: BillingPaymentAdjustmentApprove,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.adjustment.approve")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BillingPaymentAdjustmentApprovalCommandResult:
    _set_financial_no_store(response)
    record = await service.approve_payment_adjustment(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        adjustment_id=adjustment_id,
        expected_row_version=payload.expected_row_version,
    )
    return BillingPaymentAdjustmentApprovalCommandResult(
        item=BillingPaymentAdjustmentApprovalRead.model_validate(record.result),
        applied=record.applied,
    )


@platform_router.post(
    "/tenants/{tenant_id}/payment-adjustments/{adjustment_id}/reject",
    response_model=BillingPaymentAdjustmentRejectionCommandResult,
)
async def reject_payment_adjustment(
    tenant_id: UUID,
    adjustment_id: UUID,
    payload: BillingPaymentAdjustmentReject,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.adjustment.approve")),
    ],
    service: Annotated[BillingService, Depends(_service)],
) -> BillingPaymentAdjustmentRejectionCommandResult:
    _set_financial_no_store(response)
    record = await service.reject_payment_adjustment(
        actor_user_id=user.user_id,
        actor_session_id=_platform_session_or_401(user),
        operation_id=payload.operation_id,
        tenant_id=tenant_id,
        adjustment_id=adjustment_id,
        expected_row_version=payload.expected_row_version,
        reason_code=payload.reason_code,
        reason_note=payload.reason_note,
    )
    return BillingPaymentAdjustmentRejectionCommandResult(
        item=BillingPaymentAdjustmentRejectionRead.model_validate(record.result),
        applied=record.applied,
    )


@admin_router.post(
    "/{tenant_id}/invoices",
    response_model=InvoiceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_recent_platform_capability("platform.billing.manage"))],
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
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.billing.manage")),
    ],
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
