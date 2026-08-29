"""FastAPI endpoints for the POS domain.

Two routers under one prefix file:
- /api/v1/shifts/... (shift open, current, close, z-report)
- /api/v1/sales/...  (create, items, payments, complete, prescription, refund)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    get_db,
    require_any_branch_permission,
    require_branch_permission,
    require_writable_tenant,
)
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.catalog.schemas import CatalogItemRead
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import (
    PaymentAdd,
    PaymentRead,
    POSCommandRead,
    POSFavoriteCatalogRead,
    POSFavoriteCreate,
    POSFavoriteRead,
    POSPaymentAttemptConfirm,
    POSPaymentAttemptCreate,
    POSPaymentAttemptRead,
    POSPaymentAttemptVoid,
    POSRefundAttemptConfirm,
    POSRefundAttemptCreate,
    POSRefundAttemptRead,
    POSRefundAttemptVoid,
    PrescriptionLogCreate,
    PrescriptionLogRead,
    ReceiptData,
    RefundCreate,
    SaleCheckoutRequest,
    SaleCheckoutResult,
    SaleCompleteRequest,
    SaleCreate,
    SaleDetails,
    SaleItemAdd,
    SaleItemAdded,
    SaleItemDelete,
    SaleItemDeleted,
    SaleItemPatch,
    SaleItemRead,
    SaleList,
    SaleListItem,
    SaleRead,
    SalesSummaryOverview,
    ShiftCloseRequest,
    ShiftHistoryItem,
    ShiftHistoryList,
    ShiftOpenRequest,
    ShiftRead,
    ZReport,
)
from app.domains.pos.service import POSService

router = APIRouter(prefix="/api/v1", tags=["pos"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> POSService:
    return POSService(POSRepository(db))


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


def _can_view_tenant_sales(user: CurrentUser) -> bool:
    return user.has_tenant_scope("sales.view.tenant")


def _can_manage_tenant_sales(user: CurrentUser) -> bool:
    return user.has_tenant_scope("pos.manage_sales")


def _can_manage_tenant_shifts(user: CurrentUser) -> bool:
    return user.has_tenant_scope("pos.manage_shifts")


def _sale_view_branch_scope(user: CurrentUser) -> set[UUID] | None:
    return user.branch_scope_for("sales.view.tenant")


def _sale_manage_branch_scope(user: CurrentUser) -> set[UUID] | None:
    return user.branch_scope_for("pos.manage_sales")


def _shift_manage_branch_scope(user: CurrentUser) -> set[UUID] | None:
    return user.branch_scope_for("pos.manage_shifts")


def _refund_attempt_branch_scope(user: CurrentUser) -> set[UUID] | None:
    scopes: list[set[UUID]] = []
    for permission in ("pos.refund", "pos.refund_external_confirm"):
        if permission not in user.permissions:
            continue
        scope = user.branch_scope_for(permission)
        if scope is None:
            return None
        scopes.append(scope)
    return set().union(*scopes) if scopes else None


def _effective_report_branch_id(user: CurrentUser, branch_id: UUID | None) -> UUID | None:
    branch_scope = user.branch_scope_for("reports.view")
    if branch_scope is None:
        return branch_id
    if branch_id is not None:
        if branch_id not in branch_scope:
            raise PermissionDeniedError("Branch access denied")
        return branch_id
    if len(branch_scope) == 1:
        return next(iter(branch_scope))
    raise BusinessRuleError("branch_id is required for branch-scoped reports")


# =============================================================================
# Personal POS favorites
# =============================================================================


@router.get(
    "/pos/favorites",
    response_model=list[POSFavoriteCatalogRead],
    dependencies=[Depends(require_writable_tenant)],
)
async def list_pos_favorites(
    response: Response,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
    branch_id: Annotated[UUID, Query()],
) -> list[POSFavoriteCatalogRead]:
    rows = await service.list_favorites(
        tenant_id=_current_tenant_or_400(user),
        user_id=user.user_id,
        branch_id=branch_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return [
        POSFavoriteCatalogRead(
            id=row.favorite.id,
            catalog_id=row.favorite.catalog_id,
            created_at=row.favorite.created_at,
            catalog=CatalogItemRead.model_validate(row.catalog).model_copy(
                update={"stock_available": row.stock_available}
            ),
        )
        for row in rows
    ]


@router.post(
    "/pos/favorites",
    response_model=POSFavoriteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def add_pos_favorite(
    payload: POSFavoriteCreate,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> POSFavoriteRead:
    favorite = await service.add_favorite(
        tenant_id=_current_tenant_or_400(user),
        user_id=user.user_id,
        catalog_id=payload.catalog_id,
    )
    return POSFavoriteRead.model_validate(favorite)


@router.delete(
    "/pos/favorites/{catalog_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_writable_tenant)],
)
async def remove_pos_favorite(
    catalog_id: UUID,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> Response:
    await service.remove_favorite(
        tenant_id=_current_tenant_or_400(user),
        user_id=user.user_id,
        catalog_id=catalog_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Server-trusted payment attempts
# =============================================================================


@router.post(
    "/pos/payment-attempts",
    response_model=POSPaymentAttemptRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def create_pos_payment_attempt(
    payload: POSPaymentAttemptCreate,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> POSPaymentAttemptRead:
    attempt = await service.create_payment_attempt(
        tenant_id=_current_tenant_or_400(user),
        sale_id=payload.sale_id,
        actor_id=user.user_id,
        operation_id=payload.operation_id,
        payment_method=payload.payment_method,
        amount=payload.amount,
        currency=payload.currency,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return POSPaymentAttemptRead.model_validate(attempt)


@router.get(
    "/pos/payment-attempts/{attempt_id}",
    response_model=POSPaymentAttemptRead,
)
async def get_pos_payment_attempt(
    attempt_id: UUID,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> POSPaymentAttemptRead:
    attempt = await service.get_payment_attempt(
        tenant_id=_current_tenant_or_400(user),
        attempt_id=attempt_id,
        actor_id=user.user_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return POSPaymentAttemptRead.model_validate(attempt)


@router.post(
    "/pos/payment-attempts/{attempt_id}/reconciliation",
    response_model=POSPaymentAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def begin_pos_payment_attempt_reconciliation(
    attempt_id: UUID,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> POSPaymentAttemptRead:
    attempt = await service.begin_payment_attempt_reconciliation(
        tenant_id=_current_tenant_or_400(user),
        attempt_id=attempt_id,
        actor_id=user.user_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return POSPaymentAttemptRead.model_validate(attempt)


@router.post(
    "/pos/payment-attempts/{attempt_id}/confirm",
    response_model=POSPaymentAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def confirm_pos_payment_attempt(
    attempt_id: UUID,
    payload: POSPaymentAttemptConfirm,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> POSPaymentAttemptRead:
    attempt = await service.confirm_payment_attempt(
        tenant_id=_current_tenant_or_400(user),
        attempt_id=attempt_id,
        actor_id=user.user_id,
        external_reference=payload.external_reference,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return POSPaymentAttemptRead.model_validate(attempt)


@router.post(
    "/pos/payment-attempts/{attempt_id}/void",
    response_model=POSPaymentAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def void_pos_payment_attempt(
    attempt_id: UUID,
    payload: POSPaymentAttemptVoid,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> POSPaymentAttemptRead:
    attempt = await service.void_payment_attempt(
        tenant_id=_current_tenant_or_400(user),
        attempt_id=attempt_id,
        actor_id=user.user_id,
        reason=payload.reason,
        operator_note=payload.operator_note,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return POSPaymentAttemptRead.model_validate(attempt)


# =============================================================================
# Server-controlled electronic refund attempts
# =============================================================================


@router.post(
    "/sales/{parent_id}/refund-attempts",
    response_model=POSRefundAttemptRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def create_pos_refund_attempt(
    parent_id: UUID,
    payload: POSRefundAttemptCreate,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
    ],
    service: Annotated[POSService, Depends(_service)],
) -> POSRefundAttemptRead:
    return await service.create_refund_attempt(
        tenant_id=_current_tenant_or_400(user),
        parent_sale_id=parent_id,
        items=[(item.sale_item_id, item.qty) for item in payload.items],
        actor_id=user.user_id,
        operation_id=payload.operation_id,
        can_manage_tenant=_can_manage_tenant_shifts(user),
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        allowed_manage_branch_ids=_shift_manage_branch_scope(user),
    )


@router.get(
    "/pos/refund-attempts/{attempt_id}",
    response_model=POSRefundAttemptRead,
)
async def get_pos_refund_attempt(
    attempt_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_branch_permission(
                "pos.refund", "pos.refund_external_confirm", policy="resource"
            )
        ),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> POSRefundAttemptRead:
    return await service.get_refund_attempt(
        tenant_id=_current_tenant_or_400(user),
        attempt_id=attempt_id,
        allowed_branch_ids=_refund_attempt_branch_scope(user),
    )


@router.post(
    "/pos/refund-attempts/{attempt_id}/reconciliation",
    response_model=POSRefundAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def begin_pos_refund_attempt_reconciliation(
    attempt_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("pos.refund", policy="resource")),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> POSRefundAttemptRead:
    return await service.begin_refund_attempt_reconciliation(
        tenant_id=_current_tenant_or_400(user),
        attempt_id=attempt_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )


@router.post(
    "/pos/refund-attempts/{attempt_id}/confirm",
    response_model=POSRefundAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def confirm_pos_refund_attempt(
    attempt_id: UUID,
    payload: POSRefundAttemptConfirm,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("pos.refund_external_confirm", policy="resource")),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> POSRefundAttemptRead:
    return await service.confirm_refund_attempt(
        tenant_id=_current_tenant_or_400(user),
        attempt_id=attempt_id,
        actor_id=user.user_id,
        confirmations=[
            (
                confirmation.payment_method,
                confirmation.terminal_id,
                confirmation.document_number,
            )
            for confirmation in payload.confirmations
        ],
        allowed_branch_ids=user.branch_scope_for("pos.refund_external_confirm"),
    )


@router.post(
    "/pos/refund-attempts/{attempt_id}/void",
    response_model=POSRefundAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def void_pos_refund_attempt(
    attempt_id: UUID,
    payload: POSRefundAttemptVoid,
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_branch_permission(
                "pos.refund", "pos.refund_external_confirm", policy="resource"
            )
        ),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> POSRefundAttemptRead:
    return await service.void_refund_attempt(
        tenant_id=_current_tenant_or_400(user),
        attempt_id=attempt_id,
        actor_id=user.user_id,
        reason=payload.reason,
        operator_note=payload.operator_note,
        can_manage_tenant=user.has_tenant_scope("pos.refund_external_confirm"),
        allowed_branch_ids=_refund_attempt_branch_scope(user),
        allowed_manage_branch_ids=user.branch_scope_for("pos.refund_external_confirm"),
    )


# =============================================================================
# Shifts
# =============================================================================


@router.post(
    "/shifts/open",
    response_model=ShiftRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def open_shift(
    payload: ShiftOpenRequest,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("pos.shift_open", policy="resource"))
    ],
    service: Annotated[POSService, Depends(_service)],
) -> ShiftRead:
    shift = await service.open_shift(
        tenant_id=_current_tenant_or_400(user),
        register_id=payload.register_id,
        opened_by_user_id=user.user_id,
        opening_cash=payload.opening_cash,
        allowed_branch_ids=user.branch_scope_for("pos.shift_open"),
    )
    return ShiftRead.model_validate(shift)


@router.get("/shifts/current", response_model=ShiftRead | None)
async def get_current_shift(
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_branch_permission(
                "pos.shift_open", "pos.shift_close", "pos.sell", policy="resource"
            )
        ),
    ],
    service: Annotated[POSService, Depends(_service)],
    register_id: Annotated[UUID, Query()],
) -> ShiftRead | None:
    shift = await service.get_current_shift(
        user_id=user.user_id,
        register_id=register_id,
        can_manage_tenant=_can_manage_tenant_shifts(user),
        allowed_branch_ids=user.branch_scope_for_any(
            "pos.shift_open",
            "pos.shift_close",
            "pos.sell",
        ),
        allowed_manage_branch_ids=_shift_manage_branch_scope(user),
    )
    return ShiftRead.model_validate(shift) if shift is not None else None


@router.get("/shifts", response_model=ShiftHistoryList)
async def list_shifts(
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("reports.view", policy="filter"))
    ],
    service: Annotated[POSService, Depends(_service)],
    shift_status: Annotated[
        Literal["open", "closed", "suspended"] | None,
        Query(alias="status"),
    ] = None,
    branch_id: Annotated[UUID | None, Query()] = None,
    register_id: Annotated[UUID | None, Query()] = None,
    cashier_id: Annotated[UUID | None, Query()] = None,
    cashier_query: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 25,
) -> ShiftHistoryList:
    rows, total = await service.list_shifts(
        tenant_id=_current_tenant_or_400(user),
        status=shift_status,
        branch_id=branch_id,
        register_id=register_id,
        cashier_id=cashier_id,
        cashier_query=cashier_query,
        date_from=date_from,
        date_to=date_to,
        allowed_branch_ids=user.branch_scope_for("reports.view"),
        page=page,
        page_size=page_size,
    )
    return ShiftHistoryList(
        items=[ShiftHistoryItem.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/shifts/{shift_id}/close",
    response_model=ShiftRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def close_shift(
    shift_id: UUID,
    payload: ShiftCloseRequest,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("pos.shift_close", policy="resource"))
    ],
    service: Annotated[POSService, Depends(_service)],
) -> ShiftRead:
    shift = await service.close_shift(
        shift_id=shift_id,
        closing_cash_actual=payload.closing_cash_actual,
        closed_by_user_id=user.user_id,
        can_manage_tenant=_can_manage_tenant_shifts(user),
        allowed_branch_ids=user.branch_scope_for("pos.shift_close"),
        allowed_manage_branch_ids=_shift_manage_branch_scope(user),
        notes=payload.notes,
    )
    return ShiftRead.model_validate(shift)


@router.get("/shifts/{shift_id}/z-report", response_model=ZReport)
async def z_report(
    shift_id: UUID,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("reports.view", policy="filter"))
    ],
    service: Annotated[POSService, Depends(_service)],
) -> ZReport:
    report = await service.z_report(
        shift_id,
        allowed_branch_ids=user.branch_scope_for("reports.view"),
    )
    return ZReport.model_validate(report)


@router.get(
    "/shifts/{shift_id}/z-report.xlsx",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
)
async def z_report_xlsx(
    shift_id: UUID,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("reports.view", policy="filter"))
    ],
    service: Annotated[POSService, Depends(_service)],
) -> Response:
    """Z-report as an Excel workbook (closed shifts only), lazily generated and
    cached in MinIO."""
    xlsx = await service.get_z_report_xlsx(
        shift_id,
        allowed_branch_ids=user.branch_scope_for("reports.view"),
    )
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="z-report-{shift_id}.xlsx"'},
    )


@router.get(
    "/reports/sales-summary.xlsx",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
)
async def sales_summary_xlsx(
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("reports.view", policy="filter"))
    ],
    service: Annotated[POSService, Depends(_service)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    branch_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    """Accountant sales summary over [from, to] as XLSX. Generated on the fly
    (not cached). from > to → 400; an empty period → a valid zero-total file."""
    effective_branch_id = _effective_report_branch_id(user, branch_id)
    xlsx = await service.get_sales_summary_xlsx(
        tenant_id=_current_tenant_or_400(user),
        date_from=date_from,
        date_to=date_to,
        branch_id=effective_branch_id,
    )
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="sales-summary-{date_from}_{date_to}.xlsx"'
            )
        },
    )


@router.get("/reports/sales-summary", response_model=SalesSummaryOverview)
async def sales_summary_overview(
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("reports.view", policy="filter"))
    ],
    service: Annotated[POSService, Depends(_service)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    branch_id: Annotated[UUID | None, Query()] = None,
) -> SalesSummaryOverview:
    effective_branch_id = _effective_report_branch_id(user, branch_id)
    return await service.get_sales_summary_overview(
        tenant_id=_current_tenant_or_400(user),
        date_from=date_from,
        date_to=date_to,
        branch_id=effective_branch_id,
    )


@router.get(
    "/reports/stock-on-date.xlsx",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
)
async def stock_on_date_xlsx(
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("reports.view", policy="filter"))
    ],
    service: Annotated[POSService, Depends(_service)],
    on_date: Annotated[date | None, Query(alias="date")] = None,
    branch_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    """Stock as of a date (default today) as XLSX, reconstructed from the
    batch_movement ledger. Generated on the fly (not cached). Empty stock → a
    valid zero file."""
    effective_date = on_date or date.today()
    effective_branch_id = _effective_report_branch_id(user, branch_id)
    xlsx = await service.get_stock_on_date_xlsx(
        tenant_id=_current_tenant_or_400(user),
        on_date=effective_date,
        branch_id=effective_branch_id,
    )
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="stock-{effective_date}.xlsx"'},
    )


# =============================================================================
# Sales
# =============================================================================


@router.post(
    "/sales",
    response_model=SaleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def create_sale(
    payload: SaleCreate,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleRead:
    return await service.create_sale_command(
        tenant_id=_current_tenant_or_400(user),
        register_id=payload.register_id,
        cashier_user_id=user.user_id,
        operation_id=payload.operation_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )


@router.get("/pos/commands/{operation_id}", response_model=POSCommandRead)
async def get_pos_command_result(
    operation_id: UUID,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> POSCommandRead:
    return await service.get_pos_command_result(
        tenant_id=_current_tenant_or_400(user),
        actor_user_id=user.user_id,
        operation_id=operation_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )


@router.post(
    "/sales/checkout",
    response_model=SaleCheckoutResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def checkout_sale(
    payload: SaleCheckoutRequest,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleCheckoutResult:
    if payload.prescription is not None and not (
        user.is_developer or "pos.handle_prescription" in user.permissions
    ):
        raise PermissionDeniedError("Missing permission: pos.handle_prescription")

    allowed_branch_ids = user.branch_scope_for("pos.sell")
    if payload.prescription is not None:
        allowed_branch_ids = user.branch_scope_for_all(
            "pos.sell",
            "pos.handle_prescription",
        )

    return await service.checkout(
        tenant_id=_current_tenant_or_400(user),
        register_id=payload.register_id,
        cashier_user_id=user.user_id,
        operation_id=payload.operation_id,
        draft_sale_id=payload.draft_sale_id,
        items=[(item.catalog_id, item.qty) for item in payload.items],
        payments=[
            (
                payment.payment_method,
                payment.amount,
                payment.metadata,
                payment.payment_attempt_id,
            )
            for payment in payload.payments
        ],
        prescription=(
            payload.prescription.model_dump(exclude_none=True)
            if payload.prescription is not None
            else None
        ),
        expired_sale_confirmed=payload.expired_sale_confirmed,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=allowed_branch_ids,
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )


@router.get(
    "/sales/operations/{operation_id}",
    response_model=SaleCheckoutResult,
)
async def get_checkout_result(
    operation_id: UUID,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleCheckoutResult:
    return await service.get_checkout_result(
        tenant_id=_current_tenant_or_400(user),
        operation_id=operation_id,
        actor_id=user.user_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )


@router.get(
    "/sales/refund-operations/{operation_id}",
    response_model=SaleRead,
)
async def get_refund_result(
    operation_id: UUID,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
    ],
    service: Annotated[POSService, Depends(_service)],
) -> SaleRead:
    return_sale = await service.get_refund_result(
        tenant_id=_current_tenant_or_400(user),
        operation_id=operation_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )
    return SaleRead.model_validate(return_sale)


@router.get("/sales", response_model=SaleList)
async def list_sales(
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_branch_permission("sales.view.own", "sales.view.tenant", policy="filter")
        ),
    ],
    service: Annotated[POSService, Depends(_service)],
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    receipt_number: Annotated[str | None, Query()] = None,
    branch_id: Annotated[UUID | None, Query()] = None,
    register_id: Annotated[UUID | None, Query()] = None,
    cashier_id: Annotated[UUID | None, Query()] = None,
    has_refund: Annotated[bool | None, Query()] = None,
    min_total: Annotated[Decimal | None, Query()] = None,
    max_total: Annotated[Decimal | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SaleList:
    tenant_id = _current_tenant_or_400(user)
    # Keep each capability paired with the branches where it was granted.
    # The repository applies the row-level OR: own receipts in own_scope,
    # plus every receipt in tenant_view_scope.
    own_branch_ids = user.branch_scope_for("sales.view.own")
    tenant_view_branch_ids = user.branch_scope_for("sales.view.tenant")
    branch_ids = user.branch_scope_for_any("sales.view.own", "sales.view.tenant")
    if branch_ids is not None and branch_id is not None and branch_id not in branch_ids:
        return SaleList(items=[], total=0, page=page, page_size=page_size)

    rows, total = await service.list_sales(
        tenant_id=tenant_id,
        cashier_id=cashier_id,
        branch_id=branch_id,
        register_id=register_id,
        receipt_number=receipt_number,
        date_from=date_from,
        date_to=date_to,
        has_refund=has_refund,
        min_total=min_total,
        max_total=max_total,
        branch_ids=branch_ids,
        page=page,
        page_size=page_size,
        viewer_id=user.user_id,
        own_branch_ids=own_branch_ids,
        tenant_view_branch_ids=tenant_view_branch_ids,
        can_view_tenant=_can_view_tenant_sales(user),
    )
    return SaleList(
        items=[SaleListItem(**row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/sales/{sale_id}", response_model=SaleDetails)
async def get_sale(
    sale_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_branch_permission("sales.view.own", "sales.view.tenant", policy="filter")
        ),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> SaleDetails:
    sale, items, payments = await service.get_sale_details(
        sale_id,
        viewer_id=user.user_id,
        can_view_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for_any(
            "sales.view.own",
            "sales.view.tenant",
        ),
        allowed_view_branch_ids=_sale_view_branch_scope(user),
    )
    lifecycle = await service.get_sale_lifecycle(sale)
    refunded_quantities = (
        await service.get_refunded_quantities(sale.id) if sale.sale_type == "sale" else {}
    )
    return SaleDetails(
        **SaleRead.model_validate(sale).model_copy(update=lifecycle).model_dump(),
        items=[
            SaleItemRead.model_validate(si).model_copy(
                update={
                    "batch_number": batch_number,
                    "expires_at": expires_at,
                    "days_to_expiry": days_to_expiry,
                    "refunded_qty": refunded_quantities.get(si.id, Decimal("0")),
                }
            )
            for (si, batch_number, expires_at, days_to_expiry) in items
        ],
        payments=[PaymentRead.model_validate(p) for p in payments],
    )


@router.get("/sales/{sale_id}/receipt", response_model=ReceiptData)
async def get_receipt(
    sale_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_branch_permission("sales.view.own", "sales.view.tenant", policy="filter")
        ),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> ReceiptData:
    """Fully-resolved receipt data for the browser print view (item names,
    cashier, branch header). RLS scopes it to the caller's tenant."""
    return await service.build_receipt(
        sale_id,
        viewer_id=user.user_id,
        can_view_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for_any(
            "sales.view.own",
            "sales.view.tenant",
        ),
        allowed_view_branch_ids=_sale_view_branch_scope(user),
    )


@router.get(
    "/sales/{sale_id}/receipt.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_receipt_pdf(
    sale_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_branch_permission("sales.view.own", "sales.view.tenant", policy="filter")
        ),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> Response:
    """Server-rendered PDF (A4), lazily generated and cached in MinIO."""
    pdf = await service.get_receipt_pdf(
        sale_id,
        viewer_id=user.user_id,
        can_view_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for_any(
            "sales.view.own",
            "sales.view.tenant",
        ),
        allowed_view_branch_ids=_sale_view_branch_scope(user),
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="receipt-{sale_id}.pdf"'},
    )


@router.post(
    "/sales/{sale_id}/items",
    response_model=SaleItemAdded,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def add_sale_item(
    sale_id: UUID,
    payload: SaleItemAdd,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleItemAdded:
    return await service.add_item_command(
        tenant_id=_current_tenant_or_400(user),
        sale_id=sale_id,
        catalog_id=payload.catalog_id,
        qty=payload.qty,
        expired_sale_confirmed=payload.expired_sale_confirmed,
        actor_id=user.user_id,
        operation_id=payload.operation_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )


@router.patch(
    "/sales/{sale_id}/items/{item_id}",
    response_model=SaleItemRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def update_sale_item(
    sale_id: UUID,
    item_id: UUID,
    payload: SaleItemPatch,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleItemRead:
    return await service.update_item_command(
        tenant_id=_current_tenant_or_400(user),
        sale_id=sale_id,
        item_id=item_id,
        qty=payload.qty,
        actor_id=user.user_id,
        operation_id=payload.operation_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )


@router.delete(
    "/sales/{sale_id}/items/{item_id}",
    response_model=SaleItemDeleted,
    dependencies=[Depends(require_writable_tenant)],
)
async def delete_sale_item(
    sale_id: UUID,
    item_id: UUID,
    payload: SaleItemDelete,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleItemDeleted:
    return await service.delete_item_command(
        tenant_id=_current_tenant_or_400(user),
        sale_id=sale_id,
        item_id=item_id,
        actor_id=user.user_id,
        operation_id=payload.operation_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )


@router.post(
    "/sales/{sale_id}/payments",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def add_payment(
    sale_id: UUID,
    payload: PaymentAdd,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
) -> PaymentRead:
    payment = await service.add_payment(
        sale_id=sale_id,
        payment_method=payload.payment_method,
        amount=payload.amount,
        operation_id=payload.operation_id,
        actor_id=user.user_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
        metadata=payload.metadata,
    )
    return PaymentRead.model_validate(payment)


@router.post(
    "/sales/{sale_id}/complete",
    response_model=SaleRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def complete_sale(
    sale_id: UUID,
    user: Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))],
    service: Annotated[POSService, Depends(_service)],
    payload: Annotated[SaleCompleteRequest | None, Body()] = None,
) -> SaleRead:
    sale = await service.complete(
        sale_id=sale_id,
        actor_id=user.user_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
        expired_sale_confirmed=(payload.expired_sale_confirmed if payload is not None else False),
    )
    return SaleRead.model_validate(sale)


@router.post(
    "/sales/{sale_id}/prescription",
    response_model=PrescriptionLogRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def add_prescription(
    sale_id: UUID,
    payload: PrescriptionLogCreate,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("pos.handle_prescription", policy="resource")),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> PrescriptionLogRead:
    pl = await service.add_prescription(
        sale_id=sale_id,
        fields=payload.model_dump(exclude_none=True),
        actor_id=user.user_id,
        can_manage_tenant=_can_manage_tenant_sales(user),
        allowed_branch_ids=user.branch_scope_for("pos.handle_prescription"),
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return PrescriptionLogRead.model_validate(pl)


@router.post(
    "/sales/{parent_id}/refund",
    response_model=SaleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def refund_sale(
    parent_id: UUID,
    payload: RefundCreate,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
    ],
    service: Annotated[POSService, Depends(_service)],
) -> SaleRead:
    return_sale = await service.refund(
        parent_sale_id=parent_id,
        items=[(i.sale_item_id, i.qty) for i in payload.items],
        reason=payload.reason,
        comment=payload.comment,
        cashier_user_id=user.user_id,
        operation_id=payload.operation_id,
        refund_attempt_id=payload.refund_attempt_id,
        can_manage_tenant=_can_manage_tenant_shifts(user),
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        allowed_manage_branch_ids=_shift_manage_branch_scope(user),
    )
    return SaleRead.model_validate(return_sale)


# Suppress unused-import warning
_ = NotFoundError
