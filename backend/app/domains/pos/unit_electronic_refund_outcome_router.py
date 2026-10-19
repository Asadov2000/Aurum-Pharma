"""Operator evidence and partial recovery, never a bank response or payout command."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    get_db,
    require_branch_permission,
    require_recent_account_mfa,
    require_writable_tenant,
)
from app.core.errors import BusinessRuleError
from app.domains.pos.unit_electronic_refund_outcome_repository import (
    UnitElectronicRefundOutcomeRepository,
)
from app.domains.pos.unit_electronic_refund_outcome_schemas import (
    ElectronicRefundConfirmation,
    ReconciledElectronicRefund,
)
from app.domains.pos.unit_electronic_refund_outcome_service import (
    UnitElectronicRefundOutcomeService,
)

router = APIRouter(prefix="/sales/{parent_sale_id}/unit-electronic-refunds/reviews/{operation_id}")
OutcomeUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
OutcomeSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Electronic refund evidence requires a tenant")
    return user.tenant_id


def _confirmation_scope(user: CurrentUser) -> set[UUID] | None:
    refund = user.branch_scope_for("pos.refund")
    confirmation = user.branch_scope_for("pos.refund_external_confirm")
    if refund is None:
        return confirmation
    if confirmation is None:
        return refund
    return refund & confirmation


@router.post(
    "/confirmations",
    response_model=ReconciledElectronicRefund,
    dependencies=[
        Depends(require_writable_tenant),
        Depends(require_recent_account_mfa),
        Depends(require_branch_permission("pos.refund_external_confirm", policy="resource")),
    ],
)
async def record_unit_electronic_refund_confirmation(
    parent_sale_id: UUID,
    operation_id: UUID4,
    payload: ElectronicRefundConfirmation,
    user: OutcomeUser,
    db: OutcomeSession,
    response: Response,
) -> ReconciledElectronicRefund:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitElectronicRefundOutcomeService(
        UnitElectronicRefundOutcomeRepository(db)
    ).record(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=_confirmation_scope(user),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
        confirmation=payload,
    )


@router.get("/confirmations", response_model=ReconciledElectronicRefund)
async def read_unit_electronic_refund_confirmations(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: OutcomeUser,
    db: OutcomeSession,
    response: Response,
) -> ReconciledElectronicRefund:
    response.headers["Cache-Control"] = "private, no-store"
    return await UnitElectronicRefundOutcomeService(UnitElectronicRefundOutcomeRepository(db)).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )
