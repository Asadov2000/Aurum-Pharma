"""Scoped completion and uncached recovery of explicitly test-only sales."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.training_checkout_repository import TrainingCheckoutRepository
from app.domains.pos.training_checkout_schemas import (
    TrainingCheckoutOperationRead,
    TrainingCheckoutRequest,
    TrainingCheckoutResultV1,
)
from app.domains.pos.training_checkout_service import TrainingCheckoutService

router = APIRouter(prefix="/sales/{sale_id}/training")
TrainingUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))
]
TrainingSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


@router.post(
    "/complete",
    response_model=TrainingCheckoutResultV1,
    dependencies=[Depends(require_writable_tenant)],
)
async def complete_training_sale(
    sale_id: UUID,
    payload: TrainingCheckoutRequest,
    user: TrainingUser,
    db: TrainingSession,
    response: Response,
) -> TrainingCheckoutResultV1:
    response.headers["Cache-Control"] = "private, no-store"
    if payload.sale_id != sale_id:
        raise BusinessRuleError("Training command sale differs from the requested sale")
    return await TrainingCheckoutService(TrainingCheckoutRepository(db)).complete(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        request=payload,
    )


@router.get("/operations/{operation_id}", response_model=TrainingCheckoutOperationRead)
async def read_training_operation(
    sale_id: UUID,
    operation_id: UUID4,
    user: TrainingUser,
    db: TrainingSession,
    response: Response,
) -> TrainingCheckoutOperationRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await TrainingCheckoutService(TrainingCheckoutRepository(db)).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
    )
