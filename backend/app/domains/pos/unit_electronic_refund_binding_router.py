"""Exclusive refund preparation and final cancellation, never payout execution."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError, ConflictError
from app.domains.pos.unit_electronic_refund_binding_repository import (
    UnitElectronicRefundBindingRepository,
)
from app.domains.pos.unit_electronic_refund_binding_schemas import UnitElectronicRefundBindingRead
from app.domains.pos.unit_electronic_refund_binding_service import (
    UnitElectronicRefundBindingService,
)

router = APIRouter(prefix="/sales/{parent_sale_id}/unit-electronic-refunds/reviews/{operation_id}")
BindingUser = Annotated[
    CurrentUser,
    Depends(require_branch_permission("pos.refund", policy="resource")),
]
BindingSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


@router.post(
    "/bind",
    response_model=UnitElectronicRefundBindingRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def bind_unit_electronic_refund_review(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: BindingUser,
    db: BindingSession,
    response: Response,
) -> UnitElectronicRefundBindingRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitElectronicRefundBindingService(UnitElectronicRefundBindingRepository(db)).bind(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )


@router.post(
    "/cancel-binding",
    response_model=UnitElectronicRefundBindingRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def cancel_unit_electronic_refund_binding(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: BindingUser,
    db: BindingSession,
    response: Response,
) -> UnitElectronicRefundBindingRead:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await UnitElectronicRefundBindingService(
            UnitElectronicRefundBindingRepository(db)
        ).cancel(
            tenant_id=_tenant(user),
            actor_id=user.user_id,
            allowed_branch_ids=user.branch_scope_for("pos.refund"),
            parent_sale_id=parent_sale_id,
            operation_id=operation_id,
        )
    except IntegrityError as error:
        if getattr(
            error.orig, "sqlstate", None
        ) == "23514" and "unit_electronic_refund_activation_cancel_denied" in str(error.orig):
            raise ConflictError("Activated refund preparation cannot be cancelled") from None
        raise


@router.get("/binding", response_model=UnitElectronicRefundBindingRead)
async def read_unit_electronic_refund_binding(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: BindingUser,
    db: BindingSession,
    response: Response,
) -> UnitElectronicRefundBindingRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitElectronicRefundBindingService(UnitElectronicRefundBindingRepository(db)).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )
