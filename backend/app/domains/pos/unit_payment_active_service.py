"""Discover saved preparation under current real cashier and branch permissions."""

from uuid import UUID

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_payment_active_repository import UnitPaymentActiveRepository
from app.domains.pos.unit_payment_active_schemas import UnitPaymentActiveRead
from app.domains.pos.unit_payment_binding_service import UnitPaymentBindingService
from app.domains.pos.unit_payment_intent_service import UnitPaymentIntentService


class UnitPaymentActiveService:
    def __init__(self, repository: UnitPaymentActiveRepository) -> None:
        self.repository = repository
        self.cart = UnitCartRepository(repository.session)

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
    ) -> UnitPaymentActiveRead:
        UnitPaymentIntentService._scope(tenant_id, actor_id, allowed_branch_ids)
        if not isinstance(sale_id, UUID):
            raise BusinessRuleError("Payment preparation sale must be a UUID")
        with self.repository.session.no_autoflush:
            await self.cart.assert_actor(tenant_id, actor_id)
            rows = await self.repository.snapshot(tenant_id, sale_id)
            if not rows:
                raise NotFoundError("Sale not found")
            row = rows[0]
            if row.cashier_id != actor_id or (
                allowed_branch_ids is not None and row.branch_id not in allowed_branch_ids
            ):
                raise PermissionDeniedError("Sale belongs to another cashier or branch")
            await self.cart.assert_actor(tenant_id, actor_id, row.branch_id)
            if len(rows) != 1:
                raise AurumError("Sale has inconsistent active payment preparation")
            if row.binding is None:
                if row.intent is not None:
                    raise AurumError("Active payment preparation is inconsistent")
                return UnitPaymentActiveRead(sale_id=sale_id, intent=None, binding=None)
            intent = row.intent
            if intent is None or (
                intent.tenant_id,
                intent.sale_id,
                intent.branch_id,
                intent.cashier_user_id,
                intent.register_id,
                intent.shift_id,
                intent.created_by,
            ) != (
                tenant_id, sale_id, row.branch_id, actor_id, row.register_id, row.shift_id, actor_id
            ):
                raise AurumError("Stored payment preparation scope is inconsistent")
            saved = UnitPaymentIntentService._result(intent)
            binding = UnitPaymentBindingService._result(saved, row.binding, None)
            return UnitPaymentActiveRead(sale_id=sale_id, intent=saved, binding=binding)
