"""Scoped intake and read-only recovery without payments or receipt creation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.mixed_zero_return_repository import MixedZeroReturnRepository
from app.domains.pos.mixed_zero_return_schemas import (
    MixedZeroReturnRead,
    MixedZeroReturnRequest,
    MixedZeroReturnResult,
)
from app.domains.pos.mixed_zero_return_service import MixedZeroReturnService

router = APIRouter(prefix="/sales/{parent_sale_id}/mixed-zero-returns")
RefundUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
RefundSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Mixed return requires a tenant")
    return user.tenant_id


@router.post(
    "/complete",
    response_model=MixedZeroReturnResult,
    dependencies=[Depends(require_writable_tenant)],
)
async def complete_mixed_zero_return(
    parent_sale_id: UUID,
    payload: MixedZeroReturnRequest,
    user: RefundUser,
    db: RefundSession,
    response: Response,
) -> MixedZeroReturnResult:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedZeroReturnService(MixedZeroReturnRepository(db)).receive(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        parent_sale_id=parent_sale_id,
        operation_id=payload.operation_id,
        selections=payload.selections(),
        expected_revision_hash=payload.expected_revision_hash,
        reason_code=payload.reason_code,
        comment=payload.comment,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )


@router.get("/operations/{operation_id}", response_model=MixedZeroReturnRead)
async def read_mixed_zero_return(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: RefundUser,
    db: RefundSession,
    response: Response,
) -> MixedZeroReturnRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedZeroReturnService(MixedZeroReturnRepository(db)).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )
