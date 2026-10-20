"""Explicit physical electronic checkout commands and GET-only recovery."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.schemas import (
    POSPaymentAttemptConfirm,
    POSPaymentAttemptRead,
    POSPaymentAttemptVoid,
)
from app.domains.pos.unit_electronic_checkout_api_schemas import (
    ElectronicCheckoutCommand,
    PrepareElectronicCheckoutAttempts,
)
from app.domains.pos.unit_electronic_checkout_repository import UnitElectronicCheckoutRepository
from app.domains.pos.unit_electronic_checkout_service import UnitElectronicCheckoutService

router = APIRouter(prefix="/sales/{sale_id}/unit-payment-plan/intents/{operation_id}")
CheckoutUser = Annotated[
    CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))
]
CheckoutSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _tenant(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Electronic checkout requires a tenant")
    return user.tenant_id


def _service(db: AsyncSession, response: Response) -> UnitElectronicCheckoutService:
    response.headers["Cache-Control"] = "private, no-store"
    return UnitElectronicCheckoutService(UnitElectronicCheckoutRepository(db))


async def _write_branches(
    service: UnitElectronicCheckoutService, user: CurrentUser, sale_id: UUID, operation_id: UUID
) -> set[UUID]:
    branches = user.branch_scope_for("pos.sell")
    if branches is not None:
        return branches
    saved = await service.intents.read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=None,
        sale_id=sale_id,
        operation_id=operation_id,
    )
    return {saved.plan.quote.branch_id}


@router.post(
    "/attempts",
    response_model=tuple[POSPaymentAttemptRead, ...],
    dependencies=[Depends(require_writable_tenant)],
)
async def prepare_unit_electronic_attempts(
    sale_id: UUID,
    operation_id: UUID4,
    payload: PrepareElectronicCheckoutAttempts,
    user: CheckoutUser,
    db: CheckoutSession,
    response: Response,
) -> tuple[POSPaymentAttemptRead, ...]:
    service = _service(db, response)
    branches = await _write_branches(service, user, sale_id, operation_id)
    rows = await service.prepare_attempts(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=branches,
        sale_id=sale_id,
        operation_id=operation_id,
        attempt_ids=dict(payload.attempt_ids),
    )
    return tuple(POSPaymentAttemptRead.model_validate(row) for row in rows)


@router.get("/attempts", response_model=tuple[POSPaymentAttemptRead, ...])
async def read_unit_electronic_attempts(
    sale_id: UUID,
    operation_id: UUID4,
    user: CheckoutUser,
    db: CheckoutSession,
    response: Response,
) -> tuple[POSPaymentAttemptRead, ...]:
    rows = await _service(db, response).read_attempts(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
    )
    return tuple(POSPaymentAttemptRead.model_validate(row) for row in rows)


@router.post(
    "/attempts/{attempt_id}/reconciliation",
    response_model=POSPaymentAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def reconcile_unit_electronic_attempt(
    sale_id: UUID,
    operation_id: UUID4,
    attempt_id: UUID4,
    payload: ElectronicCheckoutCommand,
    user: CheckoutUser,
    db: CheckoutSession,
    response: Response,
) -> POSPaymentAttemptRead:
    row = await _service(db, response).update_attempt(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
        attempt_id=attempt_id,
        action="reconcile",
    )
    return POSPaymentAttemptRead.model_validate(row)


@router.post(
    "/attempts/{attempt_id}/confirm",
    response_model=POSPaymentAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def confirm_unit_electronic_attempt(
    sale_id: UUID,
    operation_id: UUID4,
    attempt_id: UUID4,
    payload: POSPaymentAttemptConfirm,
    user: CheckoutUser,
    db: CheckoutSession,
    response: Response,
) -> POSPaymentAttemptRead:
    row = await _service(db, response).update_attempt(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
        attempt_id=attempt_id,
        action="confirm",
        terminal_id=payload.terminal_id,
        external_reference=payload.external_reference,
    )
    return POSPaymentAttemptRead.model_validate(row)


@router.post(
    "/attempts/{attempt_id}/void",
    response_model=POSPaymentAttemptRead,
    dependencies=[Depends(require_writable_tenant)],
)
async def void_unit_electronic_attempt(
    sale_id: UUID,
    operation_id: UUID4,
    attempt_id: UUID4,
    payload: POSPaymentAttemptVoid,
    user: CheckoutUser,
    db: CheckoutSession,
    response: Response,
) -> POSPaymentAttemptRead:
    row = await _service(db, response).update_attempt(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
        attempt_id=attempt_id,
        action="void",
        terminal_id=payload.terminal_id,
        external_reference=payload.external_reference,
        reason=payload.reason,
        operator_note=payload.operator_note,
        can_manage_tenant=user.has_tenant_scope("pos.manage_sales"),
        allowed_manage_branch_ids=user.branch_scope_for("pos.manage_sales"),
    )
    return POSPaymentAttemptRead.model_validate(row)


@router.post(
    "/complete",
    response_model=PhysicalCheckoutResultV3,
    dependencies=[Depends(require_writable_tenant)],
)
async def complete_unit_electronic_checkout(
    sale_id: UUID,
    operation_id: UUID4,
    payload: ElectronicCheckoutCommand,
    user: CheckoutUser,
    db: CheckoutSession,
    response: Response,
) -> PhysicalCheckoutResultV3:
    service = _service(db, response)
    branches = await _write_branches(service, user, sale_id, operation_id)
    return await service.complete(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=branches,
        sale_id=sale_id,
        operation_id=operation_id,
    )


@router.get("/checkout", response_model=PhysicalCheckoutResultV3)
async def read_unit_electronic_checkout(
    sale_id: UUID,
    operation_id: UUID4,
    user: CheckoutUser,
    db: CheckoutSession,
    response: Response,
) -> PhysicalCheckoutResultV3:
    return await _service(db, response).read(
        tenant_id=_tenant(user),
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
    )
