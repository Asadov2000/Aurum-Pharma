"""Uncached sale-level discovery of existing payment preparation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission
from app.core.errors import BusinessRuleError
from app.domains.pos.unit_payment_active_repository import UnitPaymentActiveRepository
from app.domains.pos.unit_payment_active_schemas import UnitPaymentActiveRead
from app.domains.pos.unit_payment_active_service import UnitPaymentActiveService

router = APIRouter(prefix="/sales/{sale_id}/unit-payment-plan")
ActiveUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))
]
ActiveSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


@router.get("/active-binding", response_model=UnitPaymentActiveRead)
async def read_active_unit_payment_binding(
    sale_id: UUID, user: ActiveUser, db: ActiveSession, response: Response
) -> UnitPaymentActiveRead:
    response.headers["Cache-Control"] = "private, no-store"
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return await UnitPaymentActiveService(UnitPaymentActiveRepository(db)).read(
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
    )
