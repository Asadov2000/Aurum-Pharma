"""Scoped intake and read-only recovery of returns without a payout."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4

from app.core.deps import require_writable_tenant
from app.domains.pos.physical_cash_refund_preview_repository import (
    PhysicalCashRefundPreviewRepository,
)
from app.domains.pos.physical_cash_refund_preview_service import PhysicalCashRefundPreviewService
from app.domains.pos.physical_cash_refund_router import RefundSession, RefundUser, _tenant
from app.domains.pos.physical_zero_return_repository import PhysicalZeroReturnRepository
from app.domains.pos.physical_zero_return_schemas import (
    PhysicalZeroReturnRead,
    PhysicalZeroReturnRequest,
    PhysicalZeroReturnResult,
)
from app.domains.pos.physical_zero_return_service import PhysicalZeroReturnService

router = APIRouter(prefix="/sales/{parent_sale_id}/physical-zero-returns")


async def _access(parent_sale_id: UUID, user: RefundUser, db: RefundSession) -> None:
    await PhysicalCashRefundPreviewService(PhysicalCashRefundPreviewRepository(db)).assert_access(
        tenant_id=_tenant(user),
        parent_sale_id=parent_sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )


@router.post(
    "/complete",
    response_model=PhysicalZeroReturnResult,
    dependencies=[Depends(require_writable_tenant)],
)
async def complete_physical_zero_return(
    parent_sale_id: UUID,
    payload: PhysicalZeroReturnRequest,
    user: RefundUser,
    db: RefundSession,
    response: Response,
) -> PhysicalZeroReturnResult:
    response.headers["Cache-Control"] = "private, no-store"
    await _access(parent_sale_id, user, db)
    return await PhysicalZeroReturnService(PhysicalZeroReturnRepository(db)).receive(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        parent_sale_id=parent_sale_id,
        operation_id=payload.operation_id,
        selections=payload.selections(),
        expected_revision_hash=payload.expected_revision_hash,
        reason_code=payload.reason_code,
        comment=payload.comment,
    )


@router.get("/operations/{operation_id}", response_model=PhysicalZeroReturnRead)
async def read_physical_zero_return(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: RefundUser,
    db: RefundSession,
    response: Response,
) -> PhysicalZeroReturnRead:
    response.headers["Cache-Control"] = "private, no-store"
    await _access(parent_sale_id, user, db)
    return await PhysicalZeroReturnService(PhysicalZeroReturnRepository(db)).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )
