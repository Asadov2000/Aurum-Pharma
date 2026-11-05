"""Scoped preview, immutable preparation and recovery before payment acceptance."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_payment_intent_repository import UnitPaymentIntentRepository
from app.domains.pos.unit_payment_intent_schemas import (
    SavedUnitPaymentIntent,
    SaveUnitPaymentIntentRequest,
)
from app.domains.pos.unit_payment_intent_service import UnitPaymentIntentService
from app.domains.pos.unit_payment_plan import (
    UnitPaymentPlanRequest,
    UnitPaymentPlanService,
    ValidatedUnitPaymentPlan,
)

router = APIRouter(prefix="/sales/{sale_id}/unit-payment-plan")
PlanUser = Annotated[CurrentUser, Depends(require_branch_permission("pos.sell", policy="resource"))]
PlanSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


@router.post("/preview", response_model=ValidatedUnitPaymentPlan)
async def preview_unit_payment_plan(
    sale_id: UUID,
    payload: UnitPaymentPlanRequest,
    user: PlanUser,
    db: PlanSession,
    response: Response,
) -> ValidatedUnitPaymentPlan:
    response.headers["Cache-Control"] = "private, no-store"
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    if payload.sale_id != sale_id:
        raise BusinessRuleError("Payment plan sale differs from the requested sale")
    if db.new or db.dirty or db.deleted:
        raise ConflictError("Persist pending draft changes before payment preview")
    cart = UnitCartRepository(db)
    await cart.assert_actor(user.tenant_id, user.user_id)
    branches = user.branch_scope_for("pos.sell")
    if branches is None:
        sale = await cart.lock_sale(user.tenant_id, sale_id)
        if sale is None:
            raise NotFoundError("Sale draft not found")
        branches = {sale.branch_id}
    return await UnitPaymentPlanService(cart).validate(
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        allowed_branch_ids=branches,
        request=payload,
    )


@router.post(
    "/intents",
    response_model=SavedUnitPaymentIntent,
    dependencies=[Depends(require_writable_tenant)],
)
async def save_unit_payment_intent(
    sale_id: UUID,
    payload: SaveUnitPaymentIntentRequest,
    user: PlanUser,
    db: PlanSession,
    response: Response,
) -> SavedUnitPaymentIntent:
    response.headers["Cache-Control"] = "private, no-store"
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    if payload.request.sale_id != sale_id:
        raise BusinessRuleError("Payment plan sale differs from the requested sale")
    return await UnitPaymentIntentService(UnitPaymentIntentRepository(db)).save(
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        request=payload,
    )


@router.get("/intents/{operation_id}", response_model=SavedUnitPaymentIntent)
async def read_unit_payment_intent(
    sale_id: UUID,
    operation_id: UUID4,
    user: PlanUser,
    db: PlanSession,
    response: Response,
) -> SavedUnitPaymentIntent:
    response.headers["Cache-Control"] = "private, no-store"
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return await UnitPaymentIntentService(UnitPaymentIntentRepository(db)).read(
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        allowed_branch_ids=user.branch_scope_for("pos.sell"),
        sale_id=sale_id,
        operation_id=operation_id,
    )
