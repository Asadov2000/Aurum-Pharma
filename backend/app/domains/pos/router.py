"""FastAPI endpoints for the POS domain.

Two routers under one prefix file:
- /api/v1/shifts/... (shift open, current, close, z-report)
- /api/v1/sales/...  (create, items, payments, complete, prescription, refund)
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db, require_permission
from app.core.errors import BusinessRuleError, NotFoundError
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import (
    PaymentAdd,
    PaymentRead,
    PrescriptionLogCreate,
    PrescriptionLogRead,
    RefundCreate,
    SaleCreate,
    SaleDetails,
    SaleItemAdd,
    SaleItemAdded,
    SaleItemPatch,
    SaleItemRead,
    SaleRead,
    ShiftCloseRequest,
    ShiftOpenRequest,
    ShiftRead,
    ZReport,
)
from app.domains.pos.service import POSService

router = APIRouter(prefix="/api/v1", tags=["pos"])


async def _service(db: Annotated[AsyncSession, Depends(get_db)]) -> POSService:
    return POSService(POSRepository(db))


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


# =============================================================================
# Shifts
# =============================================================================


@router.post(
    "/shifts/open",
    response_model=ShiftRead,
    status_code=status.HTTP_201_CREATED,
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
    )
    return ShiftRead.model_validate(shift)


@router.get("/shifts/current", response_model=ShiftRead | None)
async def get_current_shift(
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[POSService, Depends(_service)],
    register_id: Annotated[UUID, Query()],
) -> ShiftRead | None:
    shift = await service.get_current_shift(user_id=user.user_id, register_id=register_id)
    return ShiftRead.model_validate(shift) if shift is not None else None


@router.post("/shifts/{shift_id}/close", response_model=ShiftRead)
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
        notes=payload.notes,
    )
    return ShiftRead.model_validate(shift)


@router.get("/shifts/{shift_id}/z-report", response_model=ZReport)
async def z_report(
    shift_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("reports.view"))],
    service: Annotated[POSService, Depends(_service)],
) -> ZReport:
    return ZReport.model_validate(await service.z_report(shift_id))


# =============================================================================
# Sales
# =============================================================================


@router.post(
    "/sales",
    response_model=SaleRead,
    status_code=status.HTTP_201_CREATED,
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
    )
    return SaleRead.model_validate(sale)


@router.get("/sales/{sale_id}", response_model=SaleDetails)
async def get_sale(
    sale_id: UUID,
    _user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[POSService, Depends(_service)],
) -> SaleDetails:
    sale, items, payments = await service.get_sale_details(sale_id)
    return SaleDetails(
        **SaleRead.model_validate(sale).model_dump(),
        items=[SaleItemRead.model_validate(i) for i in items],
        payments=[PaymentRead.model_validate(p) for p in payments],
    )


@router.post(
    "/sales/{sale_id}/items",
    response_model=SaleItemAdded,
    status_code=status.HTTP_201_CREATED,
)
async def add_sale_item(
    sale_id: UUID,
    payload: SaleItemAdd,
    _user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleItemAdded:
    created, requires_rx = await service.add_item(
        sale_id=sale_id, catalog_id=payload.catalog_id, qty=payload.qty
    )
    return SaleItemAdded(
        items=[SaleItemRead.model_validate(i) for i in created],
        requires_prescription_log=requires_rx,
    )


@router.patch(
    "/sales/{sale_id}/items/{item_id}",
    response_model=SaleItemRead,
)
async def update_sale_item(
    sale_id: UUID,
    item_id: UUID,
    payload: SaleItemPatch,
    _user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleItemRead:
    item = await service.update_item(sale_id=sale_id, item_id=item_id, qty=payload.qty)
    return SaleItemRead.model_validate(item)


@router.delete("/sales/{sale_id}/items/{item_id}")
async def delete_sale_item(
    sale_id: UUID,
    item_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> dict[str, str]:
    await service.delete_item(sale_id=sale_id, item_id=item_id)
    return {"status": "deleted"}


@router.post(
    "/sales/{sale_id}/payments",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_payment(
    sale_id: UUID,
    payload: PaymentAdd,
    _user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> PaymentRead:
    payment = await service.add_payment(
        sale_id=sale_id,
        payment_method=payload.payment_method,
        amount=payload.amount,
        metadata=payload.metadata,
    )
    return PaymentRead.model_validate(payment)


@router.post("/sales/{sale_id}/complete", response_model=SaleRead)
async def complete_sale(
    sale_id: UUID,
    _user: Annotated[CurrentUser, Depends(require_permission("pos.sell"))],
    service: Annotated[POSService, Depends(_service)],
) -> SaleRead:
    sale = await service.complete(sale_id=sale_id)
    return SaleRead.model_validate(sale)


@router.post(
    "/sales/{sale_id}/prescription",
    response_model=PrescriptionLogRead,
    status_code=status.HTTP_201_CREATED,
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
    )
    return PrescriptionLogRead.model_validate(pl)


@router.post(
    "/sales/{parent_id}/refund",
    response_model=SaleRead,
    status_code=status.HTTP_201_CREATED,
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
    )
    return SaleRead.model_validate(return_sale)


# Suppress unused-import warning
_ = NotFoundError
