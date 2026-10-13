"""Atomic physical cash checkout; the caller owns the transaction."""

from uuid import UUID

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.inventory.unit_dispensing_v2_retail import UnitPricedCartLineV2
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_checkout_models import PhysicalCashCheckout
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_documents import (
    PhysicalCheckoutResultV3,
    PhysicalResultLineV3,
    build_physical_receipt,
)
from app.domains.pos.unit_cart_quote import PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_cash_intent_service import (
    SavedCashIntentV2,
    UnitCashIntentService,
    cash_intent_request_hash,
)
from app.domains.pos.unit_checkout_preparation import UnitCheckoutPreparationService
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from app.domains.pos.unit_contracts import CheckoutPaymentV2


class PhysicalCashCheckoutService:
    def __init__(self, repository: PhysicalCashCheckoutRepository) -> None:
        self.repository = repository

    async def _lock_real_sale(
        self,
        tenant_id: UUID,
        sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> Sale:
        cart = UnitCartRepository(self.repository.session)
        sale = await cart.lock_sale(tenant_id, sale_id)
        if sale is None:
            raise NotFoundError("Physical checkout sale not found")
        if sale.branch_id not in allowed_branch_ids or sale.cashier_user_id != actor_id:
            raise PermissionDeniedError("Physical checkout branch or cashier access denied")
        await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
        if sale.is_test:
            raise BusinessRuleError("Physical test sales are not supported yet")
        return sale

    async def save_intent(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> SavedCashIntentV2:
        """Keep incompatible confirmations out of this physical-only channel."""
        request = CashConfirmationRequestV2.model_validate(request)
        cart = UnitCartRepository(self.repository.session)
        await cart.assert_actor(tenant_id, actor_id)
        async with self.repository.session.begin_nested():
            await self._lock_real_sale(tenant_id, request.sale_id, actor_id, allowed_branch_ids)
            saved = await UnitCashIntentService(
                UnitCashIntentRepository(self.repository.session)
            ).save(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            if not all(
                isinstance(line, PhysicalUnitDraftQuoteLineV2)
                for line in saved.confirmation.quote.lines
            ):
                raise ConflictError("Physical cash confirmation requires a physical cart")
            return saved

    async def complete(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> PhysicalCheckoutResultV3:
        """Caller commits. A failure rolls back the complete operation to its savepoint."""
        request = CashConfirmationRequestV2.model_validate(request)
        cart = UnitCartRepository(self.repository.session)
        await cart.assert_actor(tenant_id, actor_id)
        async with self.repository.session.begin_nested():
            sale = await self._lock_real_sale(
                tenant_id,
                request.sale_id,
                actor_id,
                allowed_branch_ids,
            )
            saved = await UnitCashIntentService(
                UnitCashIntentRepository(self.repository.session)
            ).read_saved(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            original = await self.repository.get(tenant_id, request.operation_id)
            request_hash = cash_intent_request_hash(request)
            if original is not None:
                if original.sale_id != sale.id or original.request_hash != request_hash:
                    raise ConflictError("Physical checkout operation was reused")
                result = PhysicalCheckoutResultV3.model_validate(original.result_payload)
                if (
                    result.sale_id != sale.id
                    or result.tenant_id != tenant_id
                    or result.cashier_id != actor_id
                    or result.branch_id != sale.branch_id
                    or sale.status != "completed"
                    or result.total_amount != sale.total_amount
                    or result.receipt_number != sale.receipt_number
                    or result.completed_at != sale.completed_at
                ):
                    raise AurumError("Stored physical checkout is inconsistent")
                await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
                return result
            prepared = await UnitCheckoutPreparationService(
                UnitCheckoutRepository(self.repository.session)
            ).prepare_physical(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            if prepared.confirmation != saved:
                raise ConflictError("Physical checkout cash confirmation changed")
            # Preparation can wait for stock locks while access changes elsewhere.
            await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            quote = saved.confirmation.quote
            lines: list[PhysicalUnitDraftQuoteLineV2] = []
            for line in quote.lines:
                if not isinstance(line, PhysicalUnitDraftQuoteLineV2):
                    raise ConflictError("Physical checkout contains an incompatible line")
                lines.append(line)
            # Lazy construction avoids the existing eager incoming/router import cycle.
            from app.domains.inventory.unit_dispensing_v2_repository import (
                UnitDispensingV2Repository,
            )
            from app.domains.inventory.unit_dispensing_v2_service import PhysicalUnitLedgerV2Service

            retail = await PhysicalUnitLedgerV2Service(
                UnitDispensingV2Repository(self.repository.session)
            ).dispense_priced_cart(
                tenant_id=tenant_id,
                branch_id=sale.branch_id,
                command_id=request.operation_id,
                checkout_sale_id=sale.id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                lines=tuple(
                    UnitPricedCartLineV2(
                        id=line.id,
                        catalog_id=line.catalog_id,
                        packaging=line.packaging,
                        sale_format=line.sale_format,
                        quantity=line.quantity,
                        discount_amount=line.discount_amount,
                    )
                    for line in lines
                ),
            )
            if retail.total_amount != quote.total_amount:
                raise ConflictError("Posted physical retail total differs from cash confirmation")
            names = await self.repository.names(tenant_id, {line.catalog_id for line in lines})
            allocation = await self.repository.allocate_receipt(sale.register_id)
            if allocation is None:
                raise AurumError("Physical checkout receipt allocation failed")
            sequence, number = allocation
            # Stock posting and receipt allocation can also wait on concurrent writers.
            await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            completed_at = utc_now()
            result = PhysicalCheckoutResultV3(
                schema_version=3,
                tenant_id=tenant_id,
                branch_id=sale.branch_id,
                register_id=sale.register_id,
                shift_id=sale.shift_id,
                cashier_id=actor_id,
                event_id=request.operation_id,
                sale_id=sale.id,
                operation_id=request.operation_id,
                stock_command_id=request.operation_id,
                created_at=sale.created_at,
                completed_at=completed_at,
                receipt_number=number,
                receipt_sequence=sequence,
                is_test=sale.is_test,
                lines=tuple(
                    PhysicalResultLineV3(
                        id=line.id,
                        position=position,
                        catalog_id=line.catalog_id,
                        name=names[line.catalog_id],
                        packaging=line.packaging,
                        sale_format=line.sale_format,
                        quantity=line.quantity,
                        base_units=line.base_units,
                        original_retail_unit_price=line.original_retail_unit_price,
                        gross_amount=line.gross_amount,
                        discount_amount=line.discount_amount,
                        net_amount=line.net_amount,
                    )
                    for position, line in enumerate(retail.lines, 1)
                ),
                payments=(
                    CheckoutPaymentV2(
                        id=request.operation_id, method="cash", amount=retail.total_amount
                    ),
                ),
                total_amount=retail.total_amount,
            )
            receipt = build_physical_receipt(result)
            await self.repository.set_total(sale, result.total_amount)
            await self.repository.insert(
                PhysicalCashCheckout(
                    tenant_id=tenant_id,
                    id=request.operation_id,
                    sale_id=sale.id,
                    branch_id=sale.branch_id,
                    register_id=sale.register_id,
                    shift_id=sale.shift_id,
                    cashier_user_id=actor_id,
                    cash_intent_id=request.operation_id,
                    stock_command_id=request.operation_id,
                    payment_id=request.operation_id,
                    request_hash=request_hash,
                    result_payload=result.model_dump(mode="json"),
                    receipt_payload=receipt.model_dump(mode="json"),
                    created_by=actor_id,
                )
            )
            await self.repository.insert_payment(
                tenant_id=tenant_id,
                sale_id=sale.id,
                operation_id=request.operation_id,
                request_hash=request_hash,
                amount=result.total_amount,
            )
            await self.repository.complete_sale(sale, receipt, completed_at)
            await self.repository.validate_completion()
            await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            return result
