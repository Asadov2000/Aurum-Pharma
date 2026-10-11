"""Scoped HTTP cash confirmation, atomic completion and read-only recovery."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError, NotFoundError
from app.domains.pos.mixed_cash_checkout_repository import MixedCashCheckoutRepository
from app.domains.pos.mixed_cash_checkout_schemas import MixedCashOperationRead
from app.domains.pos.mixed_cash_checkout_service import MixedCashCheckoutService
from app.domains.pos.mixed_cash_checkout_status_repository import (
    MixedCashCheckoutStatusRepository,
)
from app.domains.pos.mixed_cash_checkout_status_service import MixedCashCheckoutStatusService
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_service import SavedCashIntentV2

router = APIRouter(prefix="/sales/{sale_id}/mixed-cash")
CashUser = Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))]
CashSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


async def _write_scope(
    sale_id: UUID, payload: CashConfirmationRequestV2, user: CurrentUser, db: AsyncSession
) -> tuple[UUID, set[UUID]]:
    if payload.sale_id != sale_id:
        raise BusinessRuleError("Cash confirmation sale differs from the requested sale")
    tenant_id = _tenant(user)
    cart = UnitCartRepository(db)
    await cart.assert_actor(tenant_id, user.user_id)
    branches = user.branch_scope_for("pos.sell")
    if branches is None:
        sale = await cart.lock_sale(tenant_id, sale_id)
        if sale is None:
            raise NotFoundError("Mixed cash sale not found")
        branches = {sale.branch_id}
    return tenant_id, branches


@router.post(
    "/intent", response_model=SavedCashIntentV2, dependencies=[Depends(require_writable_tenant)]
)
async def save_mixed_cash_intent(
    sale_id: UUID,
    payload: CashConfirmationRequestV2,
    user: CashUser,
    db: CashSession,
    response: Response,
) -> SavedCashIntentV2:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id, branches = await _write_scope(sale_id, payload, user, db)
    return await MixedCashCheckoutService(MixedCashCheckoutRepository(db)).save_intent(
        tenant_id=tenant_id, actor_id=user.user_id, allowed_branch_ids=branches, request=payload
    )


@router.post(
    "/complete",
    response_model=MixedCheckoutResultV4,
    dependencies=[Depends(require_writable_tenant)],
)
async def complete_mixed_cash_sale(
    sale_id: UUID,
    payload: CashConfirmationRequestV2,
    user: CashUser,
    db: CashSession,
    response: Response,
) -> MixedCheckoutResultV4:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id, branches = await _write_scope(sale_id, payload, user, db)
    return await MixedCashCheckoutService(MixedCashCheckoutRepository(db)).complete(
        tenant_id=tenant_id, actor_id=user.user_id, allowed_branch_ids=branches, request=payload
    )


@router.get("/operations/{operation_id}", response_model=MixedCashOperationRead)
async def read_mixed_cash_operation(
    sale_id: UUID, operation_id: UUID4, user: CashUser, db: CashSession, response: Response
) -> MixedCashOperationRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedCashCheckoutStatusService(MixedCashCheckoutStatusRepository(db)).read(
        tenant_id=_tenant(user),
        sale_id=sale_id,
        operation_id=operation_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
    )
