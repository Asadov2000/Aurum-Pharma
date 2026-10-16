"""Bind a saved plan to a draft, or permanently cancel its preparation."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
)
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_payment_binding_models import UnitPaymentBinding, UnitPaymentBindingCancel
from app.domains.pos.unit_payment_binding_repository import UnitPaymentBindingRepository
from app.domains.pos.unit_payment_binding_schemas import UnitPaymentBindingRead
from app.domains.pos.unit_payment_intent_repository import UnitPaymentIntentRepository
from app.domains.pos.unit_payment_intent_schemas import SavedUnitPaymentIntent
from app.domains.pos.unit_payment_intent_service import UnitPaymentIntentService
from app.domains.pos.unit_payment_plan import UnitPaymentPlanService


class UnitPaymentBindingService:
    def __init__(self, repository: UnitPaymentBindingRepository) -> None:
        self.repository = repository
        self.cart = UnitCartRepository(repository.session)
        self.intents = UnitPaymentIntentService(UnitPaymentIntentRepository(repository.session))

    @staticmethod
    def _identifiers(tenant_id: UUID, actor_id: UUID, sale_id: UUID, operation_id: UUID) -> None:
        identifiers = (tenant_id, actor_id, sale_id, operation_id)
        if not all(isinstance(value, UUID) for value in identifiers):
            raise BusinessRuleError("Payment binding identifiers must use UUIDs")

    def _clean_session(self) -> None:
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending draft changes before payment binding")

    async def bind(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
    ) -> UnitPaymentBindingRead:
        self._identifiers(tenant_id, actor_id, sale_id, operation_id)
        self._clean_session()
        await self.cart.assert_actor(tenant_id, actor_id)
        async with self.repository.session.begin_nested():
            await self.repository.lock_operation(tenant_id, operation_id)
            saved = await self.intents.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                sale_id=sale_id,
                operation_id=operation_id,
            )
            sale = await self.cart.lock_sale(tenant_id, sale_id)
            if sale is None:
                raise NotFoundError("Sale draft not found")
            binding, cancellation = await self.repository.get_state(tenant_id, operation_id)
            if cancellation is not None:
                raise ConflictError("Payment plan preparation was permanently cancelled")
            if binding is None:
                current = await UnitPaymentPlanService(self.cart).validate(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    allowed_branch_ids=(
                        allowed_branch_ids
                        if allowed_branch_ids is not None
                        else {saved.plan.quote.branch_id}
                    ),
                    request=saved.plan.request,
                )
                if current.plan_hash != saved.plan.plan_hash:
                    raise ConflictError("Saved payment plan differs from the current cart")
                binding = await self.repository.claim_binding(
                    UnitPaymentBinding(
                        tenant_id=tenant_id,
                        id=operation_id,
                        sale_id=sale_id,
                        branch_id=saved.plan.quote.branch_id,
                        cashier_user_id=actor_id,
                        created_by=actor_id,
                    )
                )
            await self.cart.assert_actor(tenant_id, actor_id, saved.plan.quote.branch_id)
            return self._result(saved, binding, None)

    async def cancel(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
    ) -> UnitPaymentBindingRead:
        self._identifiers(tenant_id, actor_id, sale_id, operation_id)
        self._clean_session()
        await self.cart.assert_actor(tenant_id, actor_id)
        async with self.repository.session.begin_nested():
            await self.repository.lock_operation(tenant_id, operation_id)
            saved = await self.intents.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                sale_id=sale_id,
                operation_id=operation_id,
            )
            sale = await self.cart.lock_sale(tenant_id, sale_id)
            if sale is None:
                raise NotFoundError("Sale draft not found")
            binding, cancellation = await self.repository.get_state(tenant_id, operation_id)
            if cancellation is None:
                if await self.repository.has_financial_activity(tenant_id, sale_id):
                    raise ConflictError("Payment activity prevents cancellation of preparation")
                cancellation = await self.repository.claim_cancel(
                    UnitPaymentBindingCancel(
                        tenant_id=tenant_id,
                        id=operation_id,
                        sale_id=sale_id,
                        branch_id=saved.plan.quote.branch_id,
                        cashier_user_id=actor_id,
                        created_by=actor_id,
                    )
                )
            await self.cart.assert_actor(tenant_id, actor_id, saved.plan.quote.branch_id)
            return self._result(saved, binding, cancellation)

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
    ) -> UnitPaymentBindingRead:
        self._identifiers(tenant_id, actor_id, sale_id, operation_id)
        with self.repository.session.no_autoflush:
            saved = await self.intents.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                sale_id=sale_id,
                operation_id=operation_id,
            )
            binding, cancellation = await self.repository.get_state(tenant_id, operation_id)
            return self._result(saved, binding, cancellation)

    @staticmethod
    def _result(
        saved: SavedUnitPaymentIntent,
        binding: UnitPaymentBinding | None,
        cancellation: UnitPaymentBindingCancel | None,
    ) -> UnitPaymentBindingRead:
        if binding is None and cancellation is None:
            raise NotFoundError("Payment preparation state not found")
        quote = saved.plan.quote
        try:
            expected_scope = (
                quote.tenant_id,
                saved.operation_id,
                quote.sale_id,
                quote.branch_id,
                quote.cashier_id,
                quote.cashier_id,
            )
            for record in (binding, cancellation):
                if record is None:
                    continue
                scope = (
                    record.tenant_id,
                    record.id,
                    record.sale_id,
                    record.branch_id,
                    record.cashier_user_id,
                    record.created_by,
                )
                if scope != expected_scope:
                    raise ValueError("Stored payment preparation scope differs")
            return UnitPaymentBindingRead(
                state="cancelled" if cancellation is not None else "bound",
                operation_id=saved.operation_id,
                sale_id=quote.sale_id,
                plan_hash=saved.plan.plan_hash,
                bound_at=binding.created_at if binding is not None else None,
                cancelled_at=cancellation.created_at if cancellation is not None else None,
            )
        except (ValidationError, ValueError):
            raise AurumError("Stored payment preparation is inconsistent") from None
