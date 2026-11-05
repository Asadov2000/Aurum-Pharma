"""Cancel only unposted cash attempts, serialized with the original completion."""

from uuid import UUID

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domains.pos.physical_cash_cancellation_models import PhysicalCashCancellation
from app.domains.pos.physical_cash_cancellation_repository import PhysicalCashCancellationRepository
from app.domains.pos.physical_cash_checkout_schemas import (
    PhysicalCashCancellationRead,
    PhysicalCashOperationCancelled,
    PhysicalCashOperationCompleted,
)
from app.domains.pos.physical_cash_checkout_status_repository import (
    PhysicalCashCheckoutStatusRepository,
)
from app.domains.pos.physical_cash_checkout_status_service import PhysicalCashCheckoutStatusService
from app.domains.pos.unit_cart_quote import PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_cash_intent_service import UnitCashIntentService, cash_intent_request_hash


class PhysicalCashCancellationService:
    def __init__(self, repository: PhysicalCashCancellationRepository) -> None:
        self.repository = repository

    async def cancel(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> PhysicalCashCancellationRead:
        request = CashConfirmationRequestV2.model_validate(request)
        session = self.repository.session
        cart = UnitCartRepository(session)
        await cart.assert_actor(tenant_id, actor_id)
        async with session.begin_nested():
            # Completion and cancellation use the same row lock, never a status snapshot as a fence.
            sale = await cart.lock_sale(tenant_id, request.sale_id)
            if sale is None:
                raise NotFoundError("Physical cash sale not found")
            if sale.branch_id not in allowed_branch_ids or sale.cashier_user_id != actor_id:
                raise PermissionDeniedError("Physical cash branch or cashier access denied")
            await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            if sale.is_test:
                raise BusinessRuleError("Physical test sales are not supported yet")
            saved = await UnitCashIntentService(UnitCashIntentRepository(session)).read_saved(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            if not all(
                isinstance(line, PhysicalUnitDraftQuoteLineV2)
                for line in saved.confirmation.quote.lines
            ):
                raise ConflictError("Physical cash cancellation requires a physical cart")
            status_service = PhysicalCashCheckoutStatusService(
                PhysicalCashCheckoutStatusRepository(session)
            )
            status = await status_service.read(
                tenant_id=tenant_id,
                sale_id=sale.id,
                operation_id=request.operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            if isinstance(status, PhysicalCashOperationCompleted | PhysicalCashOperationCancelled):
                return status
            if status.state != "saved_draft":
                raise NotFoundError("Saved physical cash confirmation not found")
            if (
                sale.status != "draft"
                or sale.sale_type != "sale"
                or await cart.has_payment_activity(tenant_id, sale.id)
                or await self.repository.has_stock_execution(tenant_id, sale.id)
            ):
                raise ConflictError("Physical cash sale already has financial activity")
            quote = saved.confirmation.quote
            await self.repository.insert(
                PhysicalCashCancellation(
                    tenant_id=tenant_id,
                    id=request.operation_id,
                    sale_id=sale.id,
                    branch_id=sale.branch_id,
                    register_id=quote.register_id,
                    shift_id=quote.shift_id,
                    cashier_user_id=actor_id,
                    request_hash=cash_intent_request_hash(request),
                    created_by=actor_id,
                )
            )
            terminal = await status_service.read(
                tenant_id=tenant_id,
                sale_id=sale.id,
                operation_id=request.operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            if terminal.state != "cancelled":
                raise AurumError("Physical cash cancellation was not confirmed")
            return terminal
