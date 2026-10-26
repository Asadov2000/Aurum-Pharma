"""Scoped electronic refund review and recovery, without payout authorization."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.mixed_cash_refund_api_schemas import (
    MixedRefundOptions,
    MixedRefundQuoteRequest,
)
from app.domains.pos.mixed_electronic_refund_intent_repository import (
    MixedElectronicRefundIntentRepository,
)
from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SavedMixedElectronicRefundIntent,
    SaveMixedElectronicRefundIntentRequest,
)
from app.domains.pos.mixed_electronic_refund_intent_service import (
    MixedElectronicRefundIntentService,
)
from app.domains.pos.mixed_electronic_refund_preview import MixedElectronicRefundPreview
from app.domains.pos.mixed_electronic_refund_preview_repository import (
    MixedElectronicRefundPreviewRepository,
)
from app.domains.pos.mixed_electronic_refund_preview_service import (
    MixedElectronicRefundPreviewService,
)

router = APIRouter(prefix="/sales/{parent_sale_id}/mixed-electronic-refunds")
ReviewUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
ReviewSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Mixed electronic refund review requires a tenant")
    return user.tenant_id


@router.get("/options", response_model=MixedRefundOptions)
async def read_mixed_electronic_refund_options(
    parent_sale_id: UUID,
    user: ReviewUser,
    db: ReviewSession,
    response: Response,
) -> MixedRefundOptions:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedElectronicRefundPreviewService(
        MixedElectronicRefundPreviewRepository(db)
    ).options(
        tenant_id=_tenant(user),
        parent_sale_id=parent_sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
    )


@router.post("/quote", response_model=MixedElectronicRefundPreview)
async def quote_mixed_electronic_refund(
    parent_sale_id: UUID,
    payload: MixedRefundQuoteRequest,
    user: ReviewUser,
    db: ReviewSession,
    response: Response,
) -> MixedElectronicRefundPreview:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedElectronicRefundPreviewService(
        MixedElectronicRefundPreviewRepository(db)
    ).quote(
        tenant_id=_tenant(user),
        parent_sale_id=parent_sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        request=payload,
    )


@router.post(
    "/reviews",
    response_model=SavedMixedElectronicRefundIntent,
    dependencies=[Depends(require_writable_tenant)],
)
async def save_mixed_electronic_refund_review(
    parent_sale_id: UUID,
    payload: SaveMixedElectronicRefundIntentRequest,
    user: ReviewUser,
    db: ReviewSession,
    response: Response,
) -> SavedMixedElectronicRefundIntent:
    response.headers["Cache-Control"] = "private, no-store"
    if payload.request.parent_sale_id != parent_sale_id:
        raise BusinessRuleError("Refund review sale differs from the requested sale")
    return await MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db)).save(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        request=payload,
    )


@router.get("/reviews/{operation_id}", response_model=SavedMixedElectronicRefundIntent)
async def read_mixed_electronic_refund_review(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: ReviewUser,
    db: ReviewSession,
    response: Response,
) -> SavedMixedElectronicRefundIntent:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db)).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )
