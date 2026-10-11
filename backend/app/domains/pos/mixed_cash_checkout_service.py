"""Internal mixed cash posting; one caller transaction owns both stock systems."""

from decimal import Decimal, localcontext
from uuid import UUID

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.inventory.unit_dispensing_v2_retail import (
    UnitCartRetailResultV2,
    UnitPricedCartLineV2,
)
from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_cash_checkout_repository import MixedCashCheckoutRepository
from app.domains.pos.mixed_checkout_preparation import MixedPhysicalCheckoutPreparationService
from app.domains.pos.mixed_documents import (
    LegacyResultLineV4,
    MixedCheckoutResultV4,
    build_mixed_receipt,
)
from app.domains.pos.models import Sale
from app.domains.pos.physical_documents import PhysicalResultLineV3
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_cash_intent_service import (
    SavedCashIntentV2,
    UnitCashIntentService,
    cash_intent_request_hash,
)
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from app.domains.pos.unit_contracts import EXACT_CONTEXT, CheckoutPaymentV2, LegacyResultLineV2


def _require_mixed(quote: MixedCartQuoteV2) -> None:
    if {line.inventory_kind for line in quote.lines} != {"legacy_pack", "physical_unit"}:
        raise ConflictError("Mixed cash checkout requires legacy and physical lines")


def _result_lines(
    quote: MixedCartQuoteV2, retail: UnitCartRetailResultV2, names: dict[UUID, str]
) -> tuple[LegacyResultLineV4 | PhysicalResultLineV3, ...]:
    physical = tuple(line for line in quote.lines if isinstance(line, PhysicalUnitDraftQuoteLineV2))
    if tuple(line.id for line in retail.lines) != tuple(line.id for line in physical):
        raise ConflictError("Posted physical lines differ from cash confirmation")
    posted = {line.id: line for line in retail.lines}
    lines: list[LegacyResultLineV4 | PhysicalResultLineV3] = []
    for quoted in quote.lines:
        name = names.get(quoted.catalog_id)
        if name is None:
            raise AurumError("Mixed checkout product name is unavailable")
        if isinstance(quoted, LegacyResultLineV2):
            lines.append(LegacyResultLineV4(**quoted.model_dump(), name=name))
        elif isinstance(quoted, PhysicalUnitDraftQuoteLineV2):
            actual = posted[quoted.id]
            line = PhysicalResultLineV3(
                id=actual.id,
                position=quoted.position,
                catalog_id=actual.catalog_id,
                name=name,
                packaging=actual.packaging,
                sale_format=actual.sale_format,
                quantity=actual.quantity,
                base_units=actual.base_units,
                original_retail_unit_price=actual.original_retail_unit_price,
                gross_amount=actual.gross_amount,
                discount_amount=actual.discount_amount,
                net_amount=actual.net_amount,
            )
            if line.model_dump(exclude={"name"}) != quoted.model_dump():
                raise ConflictError("Posted physical retail facts changed")
            lines.append(line)
        else:
            raise ConflictError("Mixed checkout contains a prepared line")
    return tuple(lines)


class MixedCashCheckoutService:
    def __init__(self, repository: MixedCashCheckoutRepository) -> None:
        self.repository = repository

    def _require_clean_session(self) -> None:
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending draft changes before mixed cash checkout")

    async def save_intent(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> SavedCashIntentV2:
        self._require_clean_session()
        async with self.repository.session.begin_nested():
            saved = await UnitCashIntentService(
                UnitCashIntentRepository(self.repository.session)
            ).save(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            _require_mixed(saved.confirmation.quote)
            return saved

    async def complete(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> MixedCheckoutResultV4:
        """Never commit. Any failure rolls all effects back to the caller's savepoint."""
        request = CashConfirmationRequestV2.model_validate(request)
        self._require_clean_session()
        cart = UnitCartRepository(self.repository.session)
        await cart.assert_actor(tenant_id, actor_id)
        async with self.repository.session.begin_nested():
            sale = await cart.lock_sale(tenant_id, request.sale_id)
            if sale is None:
                raise NotFoundError("Mixed checkout sale not found")
            if sale.branch_id not in allowed_branch_ids or sale.cashier_user_id != actor_id:
                raise PermissionDeniedError("Mixed checkout branch or cashier access denied")
            await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            if sale.is_test:
                raise BusinessRuleError("Mixed test sales are not supported yet")
            saved = await UnitCashIntentService(
                UnitCashIntentRepository(self.repository.session)
            ).read_saved(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            quote = saved.confirmation.quote
            _require_mixed(quote)
            original = await self.repository.get(tenant_id, request.operation_id)
            request_hash = cash_intent_request_hash(request)
            if original is not None:
                if original.sale_id != sale.id or original.request_hash != request_hash:
                    raise ConflictError("Mixed checkout operation was reused")
                result = self._replay(sale, original, saved)
                await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
                return result
            prepared = await MixedPhysicalCheckoutPreparationService(
                UnitCheckoutRepository(self.repository.session)
            ).prepare(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=request,
            )
            if prepared.confirmation != saved:
                raise ConflictError("Mixed checkout cash confirmation changed")
            await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            retail = await self._post_stock(
                sale, quote, request.operation_id, actor_id, allowed_branch_ids
            )
            physical = tuple(
                line for line in quote.lines if isinstance(line, PhysicalUnitDraftQuoteLineV2)
            )
            with localcontext(EXACT_CONTEXT):
                physical_total = sum((line.net_amount for line in physical), Decimal("0.00"))
            if retail.command_id != request.operation_id or retail.total_amount != physical_total:
                raise ConflictError("Posted physical subtotal differs from cash confirmation")
            lines = _result_lines(
                quote,
                retail,
                await self.repository.names(tenant_id, {line.catalog_id for line in quote.lines}),
            )
            allocation = await self.repository.allocate_receipt(sale.register_id)
            if allocation is None:
                raise AurumError("Mixed checkout receipt allocation failed")
            sequence, number = allocation
            await cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            completed_at = utc_now()
            result = MixedCheckoutResultV4(
                schema_version=4,
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
                lines=lines,
                payments=(
                    CheckoutPaymentV2(
                        id=request.operation_id, method="cash", amount=quote.total_amount
                    ),
                ),
                total_amount=quote.total_amount,
            )
            receipt = build_mixed_receipt(result)
            await self.repository.set_total(sale, result.total_amount)
            await self.repository.insert(
                MixedCashCheckout(
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

    async def _post_stock(
        self,
        sale: Sale,
        quote: MixedCartQuoteV2,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> UnitCartRetailResultV2:
        tenant_id = sale.tenant_id
        cart = UnitCartRepository(self.repository.session)
        # Lazy imports retain the existing incoming/router import-cycle boundary.
        from app.domains.inventory.unit_dispensing_v2_repository import (
            UnitDispensingV2Repository,
        )
        from app.domains.inventory.unit_dispensing_v2_service import PhysicalUnitLedgerV2Service
        from app.domains.pos.repository import POSRepository
        from app.domains.pos.service import POSService

        legacy = await cart.list_legacy_items(tenant_id, sale.id)
        await POSService(POSRepository(self.repository.session))._consume_sale_batches(
            sale, list(legacy), actor_id=actor_id
        )
        physical = tuple(
            line for line in quote.lines if isinstance(line, PhysicalUnitDraftQuoteLineV2)
        )
        retail = await PhysicalUnitLedgerV2Service(
            UnitDispensingV2Repository(self.repository.session)
        ).dispense_priced_cart(
            tenant_id=tenant_id,
            branch_id=sale.branch_id,
            command_id=operation_id,
            mixed_checkout_sale_id=sale.id,
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
                for line in physical
            ),
        )
        return retail

    @staticmethod
    def _replay(
        sale: Sale, original: MixedCashCheckout, saved: SavedCashIntentV2
    ) -> MixedCheckoutResultV4:
        result = MixedCheckoutResultV4.model_validate(original.result_payload)
        quote = saved.confirmation.quote
        if (
            sale.status != "completed"
            or sale.sale_type != "sale"
            or sale.parent_sale_id is not None
            or result.tenant_id != sale.tenant_id
            or result.sale_id != sale.id
            or result.branch_id != sale.branch_id
            or result.register_id != sale.register_id
            or result.shift_id != sale.shift_id
            or result.cashier_id != sale.cashier_user_id
            or result.operation_id != original.id
            or result.stock_command_id != original.stock_command_id
            or result.total_amount != sale.total_amount
            or result.total_amount != quote.total_amount
            or result.receipt_number != sale.receipt_number
            or result.receipt_sequence != sale.receipt_seq
            or result.completed_at != sale.completed_at
            or result.created_at != sale.created_at
            or result.is_test != sale.is_test
            or result.currency != sale.currency
            or tuple(line.model_dump(exclude={"name"}) for line in result.lines)
            != tuple(line.model_dump() for line in quote.lines)
            or result.payments
            != (
                CheckoutPaymentV2(id=original.payment_id, method="cash", amount=quote.total_amount),
            )
            or build_mixed_receipt(result).model_dump(mode="json") != original.receipt_payload
            or original.receipt_payload != sale.receipt_snapshot
        ):
            raise AurumError("Stored mixed checkout is inconsistent")
        return result
