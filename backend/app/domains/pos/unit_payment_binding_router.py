"""Draft preparation and final cancellation; no electronic payment is started."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.unit_payment_binding_repository import UnitPaymentBindingRepository
from app.domains.pos.unit_payment_binding_schemas import UnitPaymentBindingRead
from app.domains.pos.unit_payment_binding_service import UnitPaymentBindingService

router = APIRouter(prefix="/sales/{sale_id}/unit-payment-plan/intents/{operation_id}")
BindingUser = Annotated[
    CurrentUser,
    Depends(require_branch_permission("pos.sell", policy="resource")),
]
BindingSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


@router.post(
    "/bind", response_model=UnitPaymentBindingRead, dependencies=[Depends(require_writable_tenant)]
)
async def bind_unit_payment_plan(
    sale_id: UUID,
    operation_id: UUID4,
    user: BindingUser,
    db: BindingSession,
    response: Response,
) -> UnitPaymentBindingRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitPaymentBindingService(UnitPaymentBindingRepository(db)).bind(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
    )


@router.post(
    "/cancel-binding",
    response_model=UnitPaymentBindingRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def cancel_unit_payment_binding(
    sale_id: UUID,
    operation_id: UUID4,
    user: BindingUser,
    db: BindingSession,
    response: Response,
) -> UnitPaymentBindingRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitPaymentBindingService(UnitPaymentBindingRepository(db)).cancel(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
    )


@router.get("/binding", response_model=UnitPaymentBindingRead)
async def read_unit_payment_binding(
    sale_id: UUID,
    operation_id: UUID4,
    user: BindingUser,
    db: BindingSession,
    response: Response,
) -> UnitPaymentBindingRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitPaymentBindingService(UnitPaymentBindingRepository(db)).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
    )
