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
from app.domains.pos.mixed_electronic_refund_outcome_repository import (
    MixedElectronicRefundOutcomeRepository,
)
from app.domains.pos.mixed_electronic_refund_outcome_schemas import (
    MixedElectronicRefundConfirmation,
    ReconciledMixedElectronicRefund,
)
from app.domains.pos.mixed_electronic_refund_outcome_service import (
    MixedElectronicRefundOutcomeService,
)

router = APIRouter(prefix="/sales/{parent_sale_id}/mixed-electronic-refunds/reviews/{operation_id}")
OutcomeUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.refund", policy="resource"))
]
OutcomeSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Mixed electronic refund evidence requires a tenant")
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
    response_model=ReconciledMixedElectronicRefund,
    dependencies=[
        Depends(require_writable_tenant),
        Depends(require_recent_account_mfa),
        Depends(require_branch_permission("pos.refund_external_confirm", policy="resource")),
    ],
)
async def record_mixed_electronic_refund_confirmation(
    parent_sale_id: UUID,
    operation_id: UUID4,
    payload: MixedElectronicRefundConfirmation,
    user: OutcomeUser,
    db: OutcomeSession,
    response: Response,
) -> ReconciledMixedElectronicRefund:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedElectronicRefundOutcomeService(
        MixedElectronicRefundOutcomeRepository(db)
    ).record(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=_confirmation_scope(user),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
        confirmation=payload,
    )


@router.get("/confirmations", response_model=ReconciledMixedElectronicRefund)
async def read_mixed_electronic_refund_confirmations(
    parent_sale_id: UUID,
    operation_id: UUID4,
    user: OutcomeUser,
    db: OutcomeSession,
    response: Response,
) -> ReconciledMixedElectronicRefund:
    response.headers["Cache-Control"] = "private, no-store"
    return await MixedElectronicRefundOutcomeService(
        MixedElectronicRefundOutcomeRepository(db)
    ).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.refund"),
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
    )
