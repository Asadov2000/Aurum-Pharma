"""FastAPI endpoints for the POS domain.

Two routers under one prefix file:
- /api/v1/shifts/... (shift open, current, close, z-report)
- /api/v1/sales/...  (create, items, payments, complete, prescription, refund)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    get_db,
    require_any_permission,
    require_permission,
    require_writable_tenant,
)
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import (
    PaymentAdd,
    PaymentRead,
    PrescriptionLogCreate,
    PrescriptionLogRead,
    ReceiptData,
    RefundCreate,
    SaleCreate,
    SaleDetails,
    SaleItemAdd,
    SaleItemAdded,
    SaleItemPatch,
    SaleItemRead,
    SaleList,
    SaleListItem,
    SaleRead,
    ShiftCloseRequest,
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
    return (
        user.is_developer
        or user.is_administrator
        or (user.has_tenant_branch_access and "sales.view.tenant" in user.permissions)
    )


def _sale_view_branch_scope(user: CurrentUser) -> set[UUID] | None:
    if user.branch_scope is None or "sales.view.tenant" not in user.permissions:
        return None
    return user.branch_scope


def _sale_manage_branch_scope(user: CurrentUser) -> set[UUID] | None:
    if user.branch_scope is None or "sales.view.tenant" not in user.permissions:
        return None
    return user.branch_scope


def _effective_report_branch_id(user: CurrentUser, branch_id: UUID | None) -> UUID | None:
    branch_scope = user.branch_scope
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
    user: Annotated[CurrentUser, Depends(require_permission("pos.shift_open"))],
    service: Annotated[POSService, Depends(_service)],
) -> ShiftRead:
    shift = await service.open_shift(
        tenant_id=_current_tenant_or_400(user),
        register_id=payload.register_id,
        opened_by_user_id=user.user_id,
        opening_cash=payload.opening_cash,
        allowed_branch_ids=user.branch_scope,
    )
    return ShiftRead.model_validate(shift)


@router.get("/shifts/current", response_model=ShiftRead | None)
async def get_current_shift(
    user: Annotated[
        CurrentUser,
        Depends(require_any_permission("pos.shift_open", "pos.shift_close", "pos.sell")),
    ],
    service: Annotated[POSService, Depends(_service)],
    register_id: Annotated[UUID, Query()],
) -> ShiftRead | None:
    shift = await service.get_current_shift(
        user_id=user.user_id,
        register_id=register_id,
        allowed_branch_ids=user.branch_scope,
    )
    return ShiftRead.model_validate(shift) if shift is not None else None


@router.post(
    "/shifts/{shift_id}/close",
    response_model=ShiftRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def close_shift(
    shift_id: UUID,
    payload: ShiftCloseRequest,
    user: Annotated[CurrentUser, Depends(require_permission("pos.shift_close"))],
    service: Annotated[POSService, Depends(_service)],
) -> ShiftRead:
    shift = await service.close_shift(
        shift_id=shift_id,
        closing_cash_actual=payload.closing_cash_actual,
        closed_by_user_id=user.user_id,
        can_manage_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
        notes=payload.notes,
    )
    return ShiftRead.model_validate(shift)


@router.get("/shifts/{shift_id}/z-report", response_model=ZReport)
async def z_report(
    shift_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[POSService, Depends(_service)],
) -> ZReport:
    report = await service.z_report(shift_id, allowed_branch_ids=user.branch_scope)
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
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[POSService, Depends(_service)],
) -> Response:
    """Z-report as an Excel workbook (closed shifts only), lazily generated and
    cached in MinIO."""
    xlsx = await service.get_z_report_xlsx(shift_id, allowed_branch_ids=user.branch_scope)
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
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
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


@router.get(
    "/reports/stock-on-date.xlsx",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
)
async def stock_on_date_xlsx(
    user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
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
    user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleRead:
    sale = await service.create_sale(
        tenant_id=_current_tenant_or_400(user),
        register_id=payload.register_id,
        cashier_user_id=user.user_id,
        allowed_branch_ids=user.branch_scope,
    )
    return SaleRead.model_validate(sale)


@router.get("/sales", response_model=SaleList)
async def list_sales(
    user: Annotated[CurrentUser, Depends(require_permission("sales.view.own"))],
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
    # Scope: anyone with sales.view.tenant (owner/admin/dev) sees every
    # receipt and may filter by cashier; a seller (own-only) is pinned to
    # their own receipts regardless of the cashier_id query param.
    branch_ids = user.branch_scope
    if branch_ids is not None and branch_id is not None and branch_id not in branch_ids:
        return SaleList(items=[], total=0, page=page, page_size=page_size)

    if _can_view_tenant_sales(user) or _sale_view_branch_scope(user) is not None:
        effective_cashier = cashier_id
    else:
        effective_cashier = user.user_id
    rows, total = await service.list_sales(
        tenant_id=tenant_id,
        cashier_id=effective_cashier,
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
        Depends(require_any_permission("sales.view.own", "sales.view.tenant")),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> SaleDetails:
    sale, items, payments = await service.get_sale_details(
        sale_id,
        viewer_id=user.user_id,
        can_view_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
        allowed_view_branch_ids=_sale_view_branch_scope(user),
    )
    return SaleDetails(
        **SaleRead.model_validate(sale).model_dump(),
        items=[
            SaleItemRead.model_validate(si).model_copy(
                update={
                    "batch_number": batch_number,
                    "expires_at": expires_at,
                    "days_to_expiry": days_to_expiry,
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
        Depends(require_any_permission("sales.view.own", "sales.view.tenant")),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> ReceiptData:
    """Fully-resolved receipt data for the browser print view (item names,
    cashier, branch header). RLS scopes it to the caller's tenant."""
    return await service.build_receipt(
        sale_id,
        viewer_id=user.user_id,
        can_view_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
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
        Depends(require_any_permission("sales.view.own", "sales.view.tenant")),
    ],
    service: Annotated[POSService, Depends(_service)],
) -> Response:
    """Server-rendered PDF (A4), lazily generated and cached in MinIO."""
    pdf = await service.get_receipt_pdf(
        sale_id,
        viewer_id=user.user_id,
        can_view_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
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
    user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleItemAdded:
    created, requires_rx = await service.add_item(
        sale_id=sale_id,
        catalog_id=payload.catalog_id,
        qty=payload.qty,
        actor_id=user.user_id,
        can_manage_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return SaleItemAdded(
        items=[SaleItemRead.model_validate(i) for i in created],
        requires_prescription_log=requires_rx,
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
    user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleItemRead:
    item = await service.update_item(
        sale_id=sale_id,
        item_id=item_id,
        qty=payload.qty,
        actor_id=user.user_id,
        can_manage_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return SaleItemRead.model_validate(item)


@router.delete(
    "/sales/{sale_id}/items/{item_id}",
    dependencies=[Depends(require_writable_tenant)],
)
async def delete_sale_item(
    sale_id: UUID,
    item_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> dict[str, str]:
    await service.delete_item(
        sale_id=sale_id,
        item_id=item_id,
        actor_id=user.user_id,
        can_manage_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
    )
    return {"status": "deleted"}


@router.post(
    "/sales/{sale_id}/payments",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writable_tenant)],
)
async def add_payment(
    sale_id: UUID,
    payload: PaymentAdd,
    user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> PaymentRead:
    payment = await service.add_payment(
        sale_id=sale_id,
        payment_method=payload.payment_method,
        amount=payload.amount,
        actor_id=user.user_id,
        can_manage_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
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
    user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleRead:
    sale = await service.complete(
        sale_id=sale_id,
        actor_id=user.user_id,
        can_manage_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
        allowed_manage_branch_ids=_sale_manage_branch_scope(user),
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
    user: Annotated[CurrentUser, Depends(require_permission("pos.handle_prescription"))],
    service: Annotated[POSService, Depends(_service)],
) -> PrescriptionLogRead:
    pl = await service.add_prescription(
        sale_id=sale_id,
        fields=payload.model_dump(exclude_none=True),
        actor_id=user.user_id,
        can_manage_tenant=_can_view_tenant_sales(user),
        allowed_branch_ids=user.branch_scope,
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
    user: Annotated[CurrentUser, Depends(require_permission("pos.refund"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleRead:
    return_sale = await service.refund(
        parent_sale_id=parent_id,
        items=[(i.sale_item_id, i.qty) for i in payload.items],
        reason=payload.reason,
        comment=payload.comment,
        cashier_user_id=user.user_id,
        operation_id=payload.operation_id,
        allowed_branch_ids=user.branch_scope,
    )
    return SaleRead.model_validate(return_sale)


# Suppress unused-import warning
_ = NotFoundError
