"""Scoped mixed refund preview, atomic cash payout and read-only recovery."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.mixed_cash_refund_api_schemas import (
    MixedRefundCompleteRequest,
    MixedRefundOptions,
    MixedRefundQuote,
    MixedRefundQuoteRequest,
)
from app.domains.pos.mixed_cash_refund_preview_repository import MixedCashRefundPreviewRepository
from app.domains.pos.mixed_cash_refund_preview_service import MixedCashRefundPreviewService
from app.domains.pos.mixed_cash_refund_repository import MixedCashRefundRepository
from app.domains.pos.mixed_cash_refund_service import MixedCashRefundService
from app.domains.pos.mixed_cash_refund_status_repository import MixedCashRefundStatusRepository
from app.domains.pos.mixed_cash_refund_status_schemas import (
    MixedRefundOperationCompleted,
    MixedRefundOperationRead,
)
from app.domains.pos.mixed_cash_refund_status_service import MixedCashRefundStatusService

router = APIRouter(prefix="/sales/{parent_sale_id}/mixed-cash-refunds")
RefundUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
RefundSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Mixed refund requires a tenant")
    return user.tenant_id


@router.get("/options", response_model=MixedRefundOptions)
async def read_mixed_refund_options(
    parent_sale_id: UUID, user: RefundUser, db: RefundSession, response: Response
) -> MixedRefundOptions:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedCashRefundPreviewService(MixedCashRefundPreviewRepository(db)).options(
        tenant_id=_tenant(user),
        parent_sale_id=parent_sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )


@router.post("/quote", response_model=MixedRefundQuote)
async def quote_mixed_refund(
    parent_sale_id: UUID,
    payload: MixedRefundQuoteRequest,
    user: RefundUser,
    db: RefundSession,
    response: Response,
) -> MixedRefundQuote:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedCashRefundPreviewService(MixedCashRefundPreviewRepository(db)).quote(
        tenant_id=_tenant(user),
        parent_sale_id=parent_sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        request=payload,
    )


@router.post(
    "/complete",
    response_model=MixedRefundOperationCompleted,
    dependencies=[Depends(require_writable_tenant)],
)
async def complete_mixed_refund(
    parent_sale_id: UUID,
    payload: MixedRefundCompleteRequest,
    user: RefundUser,
    db: RefundSession,
    response: Response,
) -> MixedRefundOperationCompleted:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id = _tenant(user)
    await MixedCashRefundPreviewService(MixedCashRefundPreviewRepository(db)).assert_access(
        tenant_id=tenant_id,
        parent_sale_id=parent_sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )
    result = await MixedCashRefundService(MixedCashRefundRepository(db)).refund(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        operation_id=payload.operation_id,
        parent_sale_id=parent_sale_id,
        shift_id=payload.shift_id,
        selections=payload.selections(),
        reason_code=payload.reason_code,
        comment=payload.comment,
        expected_total_amount=payload.expected_total_amount,
        expected_revision_hash=payload.expected_revision_hash,
    )
    return MixedRefundOperationCompleted(
        operation_id=result.operation_id,
        parent_sale_id=result.parent_sale_id,
        sale_id=result.sale_id,
        total_amount=result.total_amount,
        receipt=result.receipt,
    )


@router.get("/operations/{operation_id}", response_model=MixedRefundOperationRead)
async def read_mixed_refund_operation(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: RefundUser,
    db: RefundSession,
    response: Response,
) -> MixedRefundOperationRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedCashRefundStatusService(MixedCashRefundStatusRepository(db)).read(
        tenant_id=_tenant(user),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )
