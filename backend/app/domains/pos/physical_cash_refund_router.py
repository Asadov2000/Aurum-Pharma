"""Read-only recovery endpoint; no refund payout command is exposed here."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission
from app.core.errors import BusinessRuleError
from app.domains.pos.physical_cash_refund_status_repository import (
    PhysicalCashRefundStatusRepository,
)
from app.domains.pos.physical_cash_refund_status_schemas import PhysicalRefundOperationRead
from app.domains.pos.physical_cash_refund_status_service import PhysicalCashRefundStatusService

router = APIRouter(prefix="/sales/{parent_sale_id}/physical-cash-refunds")
RefundUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
RefundSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


@router.get("/operations/{operation_id}", response_model=PhysicalRefundOperationRead)
async def read_physical_refund_operation(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: RefundUser,
    db: RefundSession,
    response: Response,
) -> PhysicalRefundOperationRead:
    response.headers["Cache-Control"] = "private, no-store"
    if user.tenant_id is None:
        raise BusinessRuleError("Physical refund requires a tenant")
    return await PhysicalCashRefundStatusService(PhysicalCashRefundStatusRepository(db)).read(
        tenant_id=user.tenant_id,
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )
