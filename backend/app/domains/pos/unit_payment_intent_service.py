"""Save immutable preparation and recover it without starting a payment."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_payment_intent_models import UnitPaymentIntent
from app.domains.pos.unit_payment_intent_repository import UnitPaymentIntentRepository
from app.domains.pos.unit_payment_intent_schemas import (
    SavedUnitPaymentIntent,
    SaveUnitPaymentIntentRequest,
)
from app.domains.pos.unit_payment_plan import (
    UnitPaymentPlanRequest,
    UnitPaymentPlanService,
    ValidatedUnitPaymentPlan,
)
from app.domains.sync.integrity import canonical_json_hash


def payment_intent_request_hash(request: UnitPaymentPlanRequest) -> str:
    request = UnitPaymentPlanRequest.model_validate(request)
    payload = request.model_dump(mode="json")
    payload["payments"] = [
        part.model_dump(mode="json")
        for part in sorted(request.payments, key=lambda part: part.method)
    ]
    return canonical_json_hash({"kind": "unit_payment_intent_request_v1", "request": payload})


class UnitPaymentIntentService:
    def __init__(self, repository: UnitPaymentIntentRepository) -> None:
        self.repository = repository
        self.cart = UnitCartRepository(repository.session)

    @staticmethod
    def _scope(tenant_id: UUID, actor_id: UUID, allowed_branch_ids: set[UUID] | None) -> None:
        if not isinstance(tenant_id, UUID) or not isinstance(actor_id, UUID):
            raise BusinessRuleError("Payment intent scope must use UUIDs")
        if allowed_branch_ids is not None and (
            not isinstance(allowed_branch_ids, set)
            or not all(isinstance(branch, UUID) for branch in allowed_branch_ids)
        ):
            raise PermissionDeniedError("Payment intent branch access is invalid")

    async def _access(
        self, intent: UnitPaymentIntent, actor_id: UUID, allowed_branch_ids: set[UUID] | None
    ) -> None:
        if intent.cashier_user_id != actor_id or (
            allowed_branch_ids is not None and intent.branch_id not in allowed_branch_ids
        ):
            raise PermissionDeniedError("Payment intent branch or cashier access denied")
        await self.cart.assert_actor(intent.tenant_id, actor_id, intent.branch_id)

    async def save(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        request: SaveUnitPaymentIntentRequest,
    ) -> SavedUnitPaymentIntent:
        request = SaveUnitPaymentIntentRequest.model_validate(request)
        self._scope(tenant_id, actor_id, allowed_branch_ids)
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending draft changes before saving payment intent")
        await self.cart.assert_actor(tenant_id, actor_id)
        async with session.begin_nested():
            # Serialize this plan UUID before taking the parent sale lock.
            await self.repository.lock_operation(tenant_id, request.operation_id)
            intent = await self.repository.get(tenant_id, request.operation_id)
            request_hash = payment_intent_request_hash(request.request)
            if intent is None:
                branches = allowed_branch_ids
                if branches is None:
                    sale = await self.cart.lock_sale(tenant_id, request.request.sale_id)
                    if sale is None:
                        raise NotFoundError("Sale draft not found")
                    branches = {sale.branch_id}
                plan = await UnitPaymentPlanService(self.cart).validate(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    allowed_branch_ids=branches,
                    request=request.request,
                )
                quote = plan.quote
                intent = await self.repository.claim(
                    UnitPaymentIntent(
                        tenant_id=tenant_id,
                        id=request.operation_id,
                        branch_id=quote.branch_id,
                        sale_id=quote.sale_id,
                        register_id=quote.register_id,
                        shift_id=quote.shift_id,
                        cashier_user_id=quote.cashier_id,
                        request_hash=request_hash,
                        plan_hash=plan.plan_hash,
                        request_payload=plan.request.model_dump(mode="json"),
                        quote_payload=quote.model_dump(mode="json"),
                        change_amount=plan.change_amount,
                        created_by=actor_id,
                    )
                )
            await self._access(intent, actor_id, allowed_branch_ids)
            if intent.request_hash != request_hash:
                raise ConflictError("Payment plan operation was used for another request")
            return self._result(intent)

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
    ) -> SavedUnitPaymentIntent:
        self._scope(tenant_id, actor_id, allowed_branch_ids)
        if not isinstance(sale_id, UUID) or not isinstance(operation_id, UUID):
            raise BusinessRuleError("Payment intent identifiers must use UUIDs")
        # GET must not flush unrelated changes in a caller-owned session.
        with self.repository.session.no_autoflush:
            await self.cart.assert_actor(tenant_id, actor_id)
            intent = await self.repository.get(tenant_id, operation_id)
            if intent is None or intent.sale_id != sale_id:
                raise NotFoundError("Saved payment plan not found")
            await self._access(intent, actor_id, allowed_branch_ids)
            return self._result(intent)

    @staticmethod
    def _result(intent: UnitPaymentIntent) -> SavedUnitPaymentIntent:
        try:
            plan = ValidatedUnitPaymentPlan.model_validate(
                {
                    "request": intent.request_payload,
                    "quote": intent.quote_payload,
                    "change_amount": intent.change_amount,
                    "plan_hash": intent.plan_hash,
                }
            )
            quote = plan.quote
            if (
                quote.tenant_id,
                quote.branch_id,
                quote.sale_id,
                quote.register_id,
                quote.shift_id,
                quote.cashier_id,
            ) != (
                intent.tenant_id,
                intent.branch_id,
                intent.sale_id,
                intent.register_id,
                intent.shift_id,
                intent.cashier_user_id,
            ) or payment_intent_request_hash(
                plan.request
            ) != intent.request_hash:
                raise ValueError("Stored payment plan scope or request differs")
            return SavedUnitPaymentIntent(
                operation_id=intent.id, saved_at=intent.created_at, plan=plan
            )
        except (ValidationError, ValueError):
            raise AurumError("Stored payment plan is inconsistent") from None
