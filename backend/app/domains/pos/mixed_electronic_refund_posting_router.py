"""Post the saved electronic refund atomically; recovery never calls a bank."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.mixed_electronic_refund_posting_repository import (
    MixedElectronicRefundPostingRepository,
)
from app.domains.pos.mixed_electronic_refund_posting_schemas import PostedMixedElectronicRefund
from app.domains.pos.mixed_electronic_refund_posting_service import (
    MixedElectronicRefundPostingService,
)
from app.domains.pos.unit_electronic_refund_posting_api_schemas import PostElectronicRefundRequest

router = APIRouter(prefix="/sales/{parent_sale_id}/mixed-electronic-refunds/reviews/{operation_id}")
PostingUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
PostingSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Mixed electronic refund posting requires a tenant")
    return user.tenant_id


@router.post(
    "/post",
    response_model=PostedMixedElectronicRefund,
    dependencies=[Depends(require_writable_tenant)],
)
async def post_mixed_electronic_refund(
    parent_sale_id: UUID,
    operation_id: UUID4,
    payload: PostElectronicRefundRequest,
    user: PostingUser,
    db: PostingSession,
    response: Response,
) -> PostedMixedElectronicRefund:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedElectronicRefundPostingService(
        MixedElectronicRefundPostingRepository(db)
    ).post(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
        shift_id=payload.shift_id,
        reason_code=payload.reason_code,
        comment=payload.comment,
    )


@router.get("/posting", response_model=PostedMixedElectronicRefund)
async def read_mixed_electronic_refund_posting(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: PostingUser,
    db: PostingSession,
    response: Response,
) -> PostedMixedElectronicRefund:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedElectronicRefundPostingService(
        MixedElectronicRefundPostingRepository(db)
    ).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )
