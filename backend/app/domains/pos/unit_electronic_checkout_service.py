"""Physical electronic settlement and scoped operator commands; no bank calls."""

from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.inventory.unit_dispensing_v2_retail import UnitCartRetailResultV2
from app.domains.pos.models import POSPaymentAttempt, Sale, SalePayment
from app.domains.pos.physical_cash_checkout_models import PhysicalCashCheckout
from app.domains.pos.physical_documents import (
    PhysicalCheckoutResultV3,
    PhysicalResultLineV3,
    build_physical_receipt,
)
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_quote import PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository
from app.domains.pos.unit_checkout_payment_service import UnitCheckoutPaymentService
from app.domains.pos.unit_contracts import CheckoutPaymentV2
from app.domains.pos.unit_electronic_checkout_preparation import (
    _require_open_register,
    prepare_electronic_physical_cart,
)
from app.domains.pos.unit_electronic_checkout_repository import UnitElectronicCheckoutRepository
from app.domains.pos.unit_payment_attempt_models import UnitPaymentAttemptBinding
from app.domains.pos.unit_payment_binding_repository import UnitPaymentBindingRepository
from app.domains.pos.unit_payment_intent_repository import UnitPaymentIntentRepository
from app.domains.pos.unit_payment_intent_schemas import SavedUnitPaymentIntent
from app.domains.pos.unit_payment_intent_service import (
    UnitPaymentIntentService,
    payment_intent_request_hash,
)
from app.domains.pos.unit_payment_plan import UnitPaymentPart


class UnitElectronicCheckoutService:
    def __init__(self, repository: UnitElectronicCheckoutRepository) -> None:
        self.repository = repository
        self.cart = UnitCartRepository(repository.session)
        self.bindings = UnitPaymentBindingRepository(repository.session)
        self.intents = UnitPaymentIntentService(UnitPaymentIntentRepository(repository.session))

    def _clean_session(self) -> None:
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending changes before electronic checkout")

    async def _source(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
        *,
        lock: bool,
    ) -> tuple[SavedUnitPaymentIntent, Sale]:
        saved = await self.intents.read(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            sale_id=sale_id,
            operation_id=operation_id,
        )
        sale = await self.repository.sale(tenant_id, sale_id, lock=lock)
        if sale is None:
            raise NotFoundError("Electronic checkout sale not found")
        quote = saved.plan.quote
        physical_only = all(isinstance(line, PhysicalUnitDraftQuoteLineV2) for line in quote.lines)
        if sale.is_test or not quote.lines or not physical_only:
            raise BusinessRuleError("Electronic checkout requires real physical positions only")
        if (sale.branch_id, sale.register_id, sale.shift_id, sale.cashier_user_id) != (
            quote.branch_id,
            quote.register_id,
            quote.shift_id,
            actor_id,
        ):
            raise PermissionDeniedError("Electronic checkout source scope differs")
        if sale.sale_type != "sale" or sale.parent_sale_id is not None or sale.currency != "TJS":
            raise BusinessRuleError("Electronic checkout requires an original TJS sale")
        binding, cancellation = await self.bindings.get_state(tenant_id, operation_id)
        if binding is None or cancellation is not None or binding.sale_id != sale_id:
            raise ConflictError("Electronic checkout requires its active bound payment plan")
        await self.cart.assert_actor(tenant_id, actor_id, sale.branch_id)
        return saved, sale

    async def prepare_attempts(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        sale_id: UUID,
        operation_id: UUID,
        attempt_ids: dict[str, UUID],
    ) -> tuple[POSPaymentAttempt, ...]:
        """Persist pending attempts before any external action. Caller owns commit."""
        self._clean_session()
        if operation_id in attempt_ids.values():
            raise BusinessRuleError("Attempt IDs must differ from the payment plan operation")
        if any(not isinstance(value, UUID) or value.version != 4 for value in attempt_ids.values()):
            raise BusinessRuleError("Electronic attempt identifiers must use UUID4")
        async with self.repository.session.begin_nested():
            await self.cart.assert_actor(tenant_id, actor_id)
            # Global POS identifiers are locked before the source sale, matching
            # the established attempt API and preventing inverse lock ordering.
            await self.repository.lock_attempt_ids(tenant_id, set(attempt_ids.values()))
            await self.bindings.lock_operation(tenant_id, operation_id)
            saved, sale = await self._source(
                tenant_id, actor_id, allowed_branch_ids, sale_id, operation_id, lock=True
            )
            methods = {part.method for part in saved.plan.request.payments if part.method != "cash"}
            if set(attempt_ids) != methods or len(set(attempt_ids.values())) != len(methods):
                raise BusinessRuleError("Attempt identifiers must match all electronic allocations")
            existing = await self.repository.bindings(tenant_id, operation_id)
            if existing:
                if {row.method: row.id for row in existing} != attempt_ids:
                    raise ConflictError("Payment preparation already uses other attempt IDs")
                attempts = await self._attempts(saved, sale, status=None, lock=True)
            else:
                await self.repository.require_unused_attempt_ids(
                    tenant_id, set(attempt_ids.values())
                )
                await prepare_electronic_physical_cart(self.cart, sale, saved)
                if await self.repository.attempts(tenant_id, sale_id, lock=True):
                    raise ConflictError("Electronic checkout has unrelated payment attempts")
                for part in sorted(saved.plan.request.payments, key=lambda part: part.method):
                    if part.method == "cash":
                        continue
                    binding = UnitPaymentAttemptBinding(
                        tenant_id=tenant_id,
                        id=attempt_ids[part.method],
                        intent_id=operation_id,
                        sale_id=sale_id,
                        branch_id=sale.branch_id,
                        cashier_user_id=actor_id,
                        method=part.method,
                        amount=part.amount,
                        currency="TJS",
                        created_by=actor_id,
                    )
                    await self.repository.insert_binding(binding)
                    await self.repository.create_attempt(
                        binding,
                        POSService._payment_attempt_operation_hash(
                            sale_id=sale_id,
                            payment_method=part.method,
                            amount=part.amount,
                            currency="TJS",
                        ),
                    )
                await self.repository.validate_attempt_bindings()
                attempts = await self._attempts(saved, sale, status="pending", lock=True)
            await self.cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            return attempts

    async def read_attempts(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
    ) -> tuple[POSPaymentAttempt, ...]:
        """Recover the entire bound set without creating an attempt or rechecking prices."""
        with self.repository.session.no_autoflush:
            saved, sale = await self._source(
                tenant_id, actor_id, allowed_branch_ids, sale_id, operation_id, lock=False
            )
            if not await self.repository.bindings(
                tenant_id, operation_id
            ) and not await self.repository.attempts(tenant_id, sale_id, lock=False):
                raise NotFoundError("Electronic payment attempts have not been prepared")
            rows = await self._attempts(saved, sale, status=None, lock=False)
            await self.cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            return rows

    async def update_attempt(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
        attempt_id: UUID,
        action: Literal["reconcile", "confirm", "void"],
        terminal_id: str | None = None,
        external_reference: str | None = None,
        reason: str | None = None,
        operator_note: str | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
        can_manage_tenant: bool = False,
    ) -> POSPaymentAttempt:
        """Explicit operator command, never a bank response or automatic retry."""
        self._clean_session()
        async with self.repository.session.begin_nested():
            await self.bindings.lock_operation(tenant_id, operation_id)
            saved, sale = await self._source(
                tenant_id, actor_id, allowed_branch_ids, sale_id, operation_id, lock=True
            )
            rows = await self._attempts(saved, sale, status=None, lock=True)
            if attempt_id not in {row.id for row in rows}:
                raise NotFoundError("Attempt does not belong to the bound payment plan")
            if action == "reconcile":
                await prepare_electronic_physical_cart(self.cart, sale, saved)
            else:
                await _require_open_register(self.cart, sale)
            service = POSService(POSRepository(self.repository.session))
            scope = {sale.branch_id}
            if action == "reconcile":
                result = await service.begin_payment_attempt_reconciliation(
                    tenant_id=tenant_id,
                    attempt_id=attempt_id,
                    actor_id=actor_id,
                    allowed_branch_ids=scope,
                )
            elif action == "confirm":
                if terminal_id is None or external_reference is None:
                    raise BusinessRuleError("Payment confirmation requires terminal evidence")
                result = await service.confirm_payment_attempt(
                    tenant_id=tenant_id,
                    attempt_id=attempt_id,
                    actor_id=actor_id,
                    allowed_branch_ids=scope,
                    terminal_id=terminal_id,
                    external_reference=external_reference,
                )
            else:
                if reason is None:
                    raise BusinessRuleError("Payment cancellation requires a reason")
                result = await service.void_payment_attempt(
                    tenant_id=tenant_id,
                    attempt_id=attempt_id,
                    actor_id=actor_id,
                    allowed_branch_ids=scope,
                    reason=reason,
                    operator_note=operator_note,
                    terminal_id=terminal_id,
                    external_reference=external_reference,
                    allowed_manage_branch_ids=allowed_manage_branch_ids,
                    can_manage_tenant=can_manage_tenant,
                )
            await self.cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            return result

    async def _attempts(
        self,
        saved: SavedUnitPaymentIntent,
        sale: Sale,
        *,
        status: str | None,
        lock: bool,
    ) -> tuple[POSPaymentAttempt, ...]:
        bindings = await self.repository.bindings(sale.tenant_id, saved.operation_id)
        actual = await self.repository.attempts(sale.tenant_id, sale.id, lock=lock)
        expected: dict[str, UnitPaymentPart] = {
            part.method: part for part in saved.plan.request.payments if part.method != "cash"
        }
        bound = {row.id: row for row in bindings}
        if len(bindings) != len(expected) or len(actual) != len(expected):
            raise BusinessRuleError("Electronic payment attempts do not match the saved plan")
        for attempt in actual:
            binding = bound.get(attempt.id)
            part = expected.get(attempt.payment_method)
            if (
                binding is None
                or part is None
                or (
                    binding.tenant_id,
                    binding.intent_id,
                    binding.sale_id,
                    binding.branch_id,
                    binding.cashier_user_id,
                    binding.method,
                    binding.amount,
                    binding.currency,
                )
                != (
                    sale.tenant_id,
                    saved.operation_id,
                    sale.id,
                    sale.branch_id,
                    sale.cashier_user_id,
                    part.method,
                    part.amount,
                    "TJS",
                )
            ):
                raise BusinessRuleError("Electronic payment binding differs from the saved plan")
            if (
                attempt.tenant_id != sale.tenant_id
                or attempt.sale_id != sale.id
                or attempt.cashier_user_id != sale.cashier_user_id
                or attempt.operation_id != attempt.id
                or attempt.amount != part.amount
                or attempt.currency != "TJS"
                or not attempt.evidence_required
                or (status is not None and attempt.status != status)
            ):
                raise BusinessRuleError("Electronic payment attempt is not ready for this checkout")
            if status in {"confirmed", "consumed"} and (
                not attempt.terminal_id
                or not attempt.external_reference
                or attempt.confirmed_at is None
                or attempt.reconciliation_started_at is None
            ):
                raise BusinessRuleError("Electronic payment has no confirmed terminal evidence")
        return actual

    @staticmethod
    def _payments(
        saved: SavedUnitPaymentIntent, attempts: tuple[POSPaymentAttempt, ...]
    ) -> tuple[CheckoutPaymentV2, ...]:
        ids = {attempt.payment_method: attempt.id for attempt in attempts}
        return tuple(
            CheckoutPaymentV2(
                id=saved.operation_id if part.method == "cash" else ids[part.method],
                method=part.method,
                amount=part.amount,
            )
            for part in sorted(saved.plan.request.payments, key=lambda part: part.method)
        )

    async def complete(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        sale_id: UUID,
        operation_id: UUID,
    ) -> PhysicalCheckoutResultV3:
        self._clean_session()
        async with self.repository.session.begin_nested():
            await self.bindings.lock_operation(tenant_id, operation_id)
            saved, sale = await self._source(
                tenant_id, actor_id, allowed_branch_ids, sale_id, operation_id, lock=True
            )
            marker = await self.repository.get(tenant_id, operation_id)
            if marker is not None:
                return await self._replay(saved, sale, marker)
            lines = await prepare_electronic_physical_cart(self.cart, sale, saved)
            attempts = await self._attempts(saved, sale, status="confirmed", lock=True)
            # Imports stay lazy because incoming/router eagerly imports the ledger.
            from app.domains.inventory.unit_dispensing_v2_repository import (
                UnitDispensingV2Repository,
            )
            from app.domains.inventory.unit_dispensing_v2_service import PhysicalUnitLedgerV2Service

            retail = await PhysicalUnitLedgerV2Service(
                UnitDispensingV2Repository(self.repository.session)
            ).dispense_priced_cart(
                tenant_id=tenant_id,
                branch_id=sale.branch_id,
                command_id=operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                checkout_sale_id=sale_id,
                lines=lines,
            )
            if retail.total_amount != saved.plan.quote.total_amount:
                raise ConflictError("Physical posting differs from the bound payment amount")
            names = await self.repository.names(tenant_id, {line.catalog_id for line in lines})
            allocation = await self.repository.allocate_receipt(sale.register_id)
            if allocation is None:
                raise AurumError("Electronic checkout receipt allocation failed")
            result = PhysicalCheckoutResultV3(
                schema_version=3,
                tenant_id=tenant_id,
                branch_id=sale.branch_id,
                register_id=sale.register_id,
                shift_id=sale.shift_id,
                cashier_id=actor_id,
                event_id=operation_id,
                sale_id=sale_id,
                operation_id=operation_id,
                stock_command_id=operation_id,
                created_at=sale.created_at,
                completed_at=utc_now(),
                receipt_number=allocation[1],
                receipt_sequence=allocation[0],
                is_test=False,
                lines=self._result_lines(retail, names),
                payments=self._payments(saved, attempts),
                total_amount=retail.total_amount,
            )
            await self._persist(sale, saved, result, attempts)
            await self.cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            return result

    @staticmethod
    def _result_lines(
        retail: UnitCartRetailResultV2, names: dict[UUID, str]
    ) -> tuple[PhysicalResultLineV3, ...]:
        return tuple(
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
        )

    async def _persist(
        self,
        sale: Sale,
        saved: SavedUnitPaymentIntent,
        result: PhysicalCheckoutResultV3,
        attempts: tuple[POSPaymentAttempt, ...],
    ) -> None:
        receipt = build_physical_receipt(result)
        request_hash = payment_intent_request_hash(saved.plan.request)
        await self.repository.insert(
            PhysicalCashCheckout(
                tenant_id=sale.tenant_id,
                id=result.operation_id,
                sale_id=sale.id,
                branch_id=sale.branch_id,
                register_id=sale.register_id,
                shift_id=sale.shift_id,
                cashier_user_id=sale.cashier_user_id,
                cash_intent_id=None,
                payment_plan_intent_id=saved.operation_id,
                stock_command_id=result.operation_id,
                payment_id=result.operation_id,
                payment_journal_version=3,
                request_hash=request_hash,
                result_payload=result.model_dump(mode="json"),
                receipt_payload=receipt.model_dump(mode="json"),
                created_by=sale.cashier_user_id,
            )
        )
        for payment in result.payments:
            await self.repository.insert_plan_payment(
                tenant_id=sale.tenant_id,
                sale_id=sale.id,
                request_hash=request_hash,
                payment=payment,
            )
        await UnitCheckoutPaymentService(
            UnitCheckoutPaymentRepository(self.repository.session)
        ).record_electronic(result)
        for attempt in attempts:
            await POSRepository(self.repository.session).update_payment_attempt(
                attempt,
                status="consumed",
                consumed_at=result.completed_at,
                updated_by=sale.cashier_user_id,
            )
        await self.repository.complete_sale(sale, receipt, result.completed_at)
        await self.repository.validate_completion()

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
    ) -> PhysicalCheckoutResultV3:
        with self.repository.session.no_autoflush:
            saved, sale = await self._source(
                tenant_id, actor_id, allowed_branch_ids, sale_id, operation_id, lock=False
            )
            marker = await self.repository.get(tenant_id, operation_id)
            if marker is None:
                raise NotFoundError("Electronic checkout has not completed")
            return await self._replay(saved, sale, marker)

    async def _replay(
        self, saved: SavedUnitPaymentIntent, sale: Sale, marker: PhysicalCashCheckout
    ) -> PhysicalCheckoutResultV3:
        try:
            result = PhysicalCheckoutResultV3.model_validate(marker.result_payload)
        except ValidationError as error:
            raise AurumError("Stored electronic checkout is inconsistent") from error
        receipt = build_physical_receipt(result)
        if (
            marker.payment_journal_version != 3
            or marker.payment_plan_intent_id != saved.operation_id
            or marker.cash_intent_id is not None
            or marker.id != saved.operation_id
            or marker.sale_id != sale.id
            or marker.request_hash != payment_intent_request_hash(saved.plan.request)
            or result.sale_id != sale.id
            or result.tenant_id != sale.tenant_id
            or result.operation_id != marker.id
            or result.stock_command_id != marker.id
            or result.branch_id != sale.branch_id
            or result.register_id != sale.register_id
            or result.shift_id != sale.shift_id
            or result.cashier_id != sale.cashier_user_id
            or result.total_amount != sale.total_amount
            or result.total_amount != saved.plan.quote.total_amount
            or result.receipt_number != sale.receipt_number
            or result.receipt_sequence != sale.receipt_seq
            or result.completed_at != sale.completed_at
            or result.created_at != sale.created_at
            or sale.status != "completed"
            or sale.is_test
            or result.is_test
            or receipt.model_dump(mode="json") != marker.receipt_payload
            or receipt.model_dump(mode="json") != sale.receipt_snapshot
            or tuple(line.model_dump(exclude={"name"}) for line in result.lines)
            != tuple(line.model_dump() for line in saved.plan.quote.lines)
        ):
            raise AurumError("Stored electronic checkout is inconsistent")
        attempts = await self._attempts(saved, sale, status="consumed", lock=False)
        if result.payments != self._payments(saved, attempts) or any(
            attempt.consumed_at != result.completed_at for attempt in attempts
        ):
            raise AurumError("Stored electronic checkout payment attempts are inconsistent")
        actual = await self.repository.payments(sale.tenant_id, sale.id)
        expected = {part.id: part for part in result.payments}
        if len(actual) != len(expected) or any(
            payment.id not in expected
            or payment.payment_method != expected[payment.id].method
            or payment.amount != expected[payment.id].amount
            or payment.currency != "TJS"
            or not self._payment_operation_matches(payment, marker.id)
            or payment.operation_hash != marker.request_hash
            or payment.payment_attempt_id
            != (None if payment.payment_method == "cash" else payment.id)
            or payment.metadata_json is not None
            for payment in actual
        ):
            raise AurumError("Stored electronic checkout payments are inconsistent")
        await UnitCheckoutPaymentService(
            UnitCheckoutPaymentRepository(self.repository.session)
        ).validate_saved(3, result)
        await self.cart.assert_actor(sale.tenant_id, sale.cashier_user_id, sale.branch_id)
        return result

    @staticmethod
    def _payment_operation_matches(payment: SalePayment, checkout_id: UUID) -> bool:
        if payment.payment_method == "cash":
            return payment.operation_id == payment.id
        return (
            payment.operation_id is not None
            and payment.operation_id.version == 4
            and payment.operation_id not in {payment.id, checkout_id}
        )
