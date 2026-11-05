"""Persist activation before any future external action; recovery never repeats one."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.unit_electronic_refund_activation_repository import (
    UnitElectronicRefundActivationRepository,
)
from app.domains.pos.unit_electronic_refund_activation_schemas import (
    ActivateElectronicRefundRequest,
    SavedElectronicRefundActivation,
)
from app.domains.pos.unit_electronic_refund_activation_service import (
    UnitElectronicRefundActivationService,
)

router = APIRouter(prefix="/sales/{parent_sale_id}/unit-electronic-refunds/reviews/{operation_id}")
ActivationUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
ActivationSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Electronic refund activation requires a tenant")
    return user.tenant_id


@router.post(
    "/activate",
    response_model=SavedElectronicRefundActivation,
    dependencies=[Depends(require_writable_tenant)],
)
async def activate_unit_electronic_refund(
    parent_sale_id: UUID,
    operation_id: UUID4,
    payload: ActivateElectronicRefundRequest,
    user: ActivationUser,
    db: ActivationSession,
    response: Response,
) -> SavedElectronicRefundActivation:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitElectronicRefundActivationService(
        UnitElectronicRefundActivationRepository(db)
    ).start(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
        shift_id=payload.shift_id,
    )


@router.get("/activation", response_model=SavedElectronicRefundActivation)
async def read_unit_electronic_refund_activation(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: ActivationUser,
    db: ActivationSession,
    response: Response,
) -> SavedElectronicRefundActivation:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitElectronicRefundActivationService(
        UnitElectronicRefundActivationRepository(db)
    ).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )
