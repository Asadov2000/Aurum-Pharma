"""Scoped electronic refund review and recovery, without payout authorization."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.physical_cash_refund_api_schemas import PhysicalRefundQuoteRequest
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import (
    SavedElectronicRefundIntent,
    SaveElectronicRefundIntentRequest,
)
from app.domains.pos.unit_electronic_refund_intent_service import UnitElectronicRefundIntentService
from app.domains.pos.unit_electronic_refund_preview import UnitElectronicRefundPreview
from app.domains.pos.unit_electronic_refund_preview_repository import (
    UnitElectronicRefundPreviewRepository,
)
from app.domains.pos.unit_electronic_refund_preview_service import (
    UnitElectronicRefundPreviewService,
)

router = APIRouter(prefix="/sales/{parent_sale_id}/unit-electronic-refunds")
ReviewUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
ReviewSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Electronic refund review requires a tenant")
    return user.tenant_id


@router.post("/quote", response_model=UnitElectronicRefundPreview)
async def quote_unit_electronic_refund(
    parent_sale_id: UUID,
    payload: PhysicalRefundQuoteRequest,
    user: ReviewUser,
    db: ReviewSession,
    response: Response,
) -> UnitElectronicRefundPreview:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitElectronicRefundPreviewService(
        UnitElectronicRefundPreviewRepository(db)
    ).quote(
        tenant_id=_tenant(user),
        parent_sale_id=parent_sale_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        request=payload,
    )


@router.post(
    "/reviews",
    response_model=SavedElectronicRefundIntent,
    dependencies=[Depends(require_writable_tenant)],
)
async def save_unit_electronic_refund_review(
    parent_sale_id: UUID,
    payload: SaveElectronicRefundIntentRequest,
    user: ReviewUser,
    db: ReviewSession,
    response: Response,
) -> SavedElectronicRefundIntent:
    response.headers["Cache-Control"] = "private, no-store"
    if payload.request.parent_sale_id != parent_sale_id:
        raise BusinessRuleError("Refund review sale differs from the requested sale")
    return await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(db)).save(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        request=payload,
    )


@router.get("/reviews/{operation_id}", response_model=SavedElectronicRefundIntent)
async def read_unit_electronic_refund_review(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: ReviewUser,
    db: ReviewSession,
    response: Response,
) -> SavedElectronicRefundIntent:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(db)).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )
