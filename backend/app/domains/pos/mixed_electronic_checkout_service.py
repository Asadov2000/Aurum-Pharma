"""Internal mixed electronic settlement; no HTTP, terminal or banking invocation."""

from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, NotFoundError
from app.core.time import utc_now
from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_cash_checkout_service import MixedCashCheckoutService, _result_lines
from app.domains.pos.mixed_documents import MixedCheckoutResultV4, build_mixed_receipt
from app.domains.pos.mixed_electronic_checkout_preparation import (
    mixed_electronic_lines,
    prepare_electronic_mixed_cart,
)
from app.domains.pos.mixed_electronic_checkout_repository import MixedElectronicCheckoutRepository
from app.domains.pos.models import POSPaymentAttempt, Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository
from app.domains.pos.unit_checkout_payment_service import UnitCheckoutPaymentService
from app.domains.pos.unit_electronic_checkout_repository import UnitElectronicCheckoutRepository
from app.domains.pos.unit_electronic_checkout_service import UnitElectronicCheckoutService
from app.domains.pos.unit_payment_intent_schemas import SavedUnitPaymentIntent
from app.domains.pos.unit_payment_intent_service import payment_intent_request_hash


class _MixedAttemptService(UnitElectronicCheckoutService):
    @staticmethod
    def _require_quote_kind(saved: SavedUnitPaymentIntent) -> None:
        mixed_electronic_lines(saved.plan.quote)

    async def _prepare_source(self, sale: Sale, saved: SavedUnitPaymentIntent) -> None:
        await prepare_electronic_mixed_cart(self.cart, sale, saved)


class MixedElectronicCheckoutService:
    def __init__(self, repository: MixedElectronicCheckoutRepository) -> None:
        self.repository = repository
        self._electronic = UnitElectronicCheckoutRepository(repository.session)
        self._attempts = _MixedAttemptService(self._electronic)
        self.cart = self._attempts.cart

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
        return await self._attempts.prepare_attempts(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            sale_id=sale_id,
            operation_id=operation_id,
            attempt_ids=attempt_ids,
        )

    async def read_attempts(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        sale_id: UUID,
        operation_id: UUID,
    ) -> tuple[POSPaymentAttempt, ...]:
        return await self._attempts.read_attempts(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            sale_id=sale_id,
            operation_id=operation_id,
        )

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
        return await self._attempts.update_attempt(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            sale_id=sale_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            action=action,
            terminal_id=terminal_id,
            external_reference=external_reference,
            reason=reason,
            operator_note=operator_note,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
            can_manage_tenant=can_manage_tenant,
        )

    async def complete(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        sale_id: UUID,
        operation_id: UUID,
    ) -> MixedCheckoutResultV4:
        """All stock, attempts, payments and receipt remain inside the caller transaction."""
        self._attempts._clean_session()
        async with self.repository.session.begin_nested():
            await self._attempts.bindings.lock_operation(tenant_id, operation_id)
            saved, sale = await self._attempts._source(
                tenant_id, actor_id, allowed_branch_ids, sale_id, operation_id, lock=True
            )
            marker = await self.repository.get(tenant_id, operation_id)
            if marker is not None:
                return await self._replay(saved, sale, marker)
            await prepare_electronic_mixed_cart(self.cart, sale, saved)
            attempts = await self._attempts._attempts(saved, sale, status="confirmed", lock=True)
            retail = await MixedCashCheckoutService(self.repository)._post_stock(
                sale, saved.plan.quote, operation_id, actor_id, allowed_branch_ids
            )
            names = await self.repository.names(
                tenant_id, {line.catalog_id for line in saved.plan.quote.lines}
            )
            allocation = await self.repository.allocate_receipt(sale.register_id)
            if allocation is None:
                raise AurumError("Mixed electronic checkout receipt allocation failed")
            result = MixedCheckoutResultV4(
                schema_version=4,
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
                lines=_result_lines(saved.plan.quote, retail, names),
                payments=self._attempts._payments(saved, attempts),
                total_amount=saved.plan.quote.total_amount,
            )
            await self._persist(sale, saved, result, attempts)
            await self.cart.assert_actor(tenant_id, actor_id, sale.branch_id)
            return result

    async def _persist(
        self,
        sale: Sale,
        saved: SavedUnitPaymentIntent,
        result: MixedCheckoutResultV4,
        attempts: tuple[POSPaymentAttempt, ...],
    ) -> None:
        receipt = build_mixed_receipt(result)
        request_hash = payment_intent_request_hash(saved.plan.request)
        await self.repository.insert(
            MixedCashCheckout(
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
            await self._electronic.insert_plan_payment(
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
    ) -> MixedCheckoutResultV4:
        """Saved result only: no quote, stock planner, financial command or backfill."""
        with self.repository.session.no_autoflush:
            saved, sale = await self._attempts._source(
                tenant_id, actor_id, allowed_branch_ids, sale_id, operation_id, lock=False
            )
            marker = await self.repository.get(tenant_id, operation_id)
            if marker is None:
                raise NotFoundError("Mixed electronic checkout has not completed")
            return await self._replay(saved, sale, marker)

    async def _replay(
        self, saved: SavedUnitPaymentIntent, sale: Sale, marker: MixedCashCheckout
    ) -> MixedCheckoutResultV4:
        try:
            result = MixedCheckoutResultV4.model_validate(marker.result_payload)
        except ValidationError as error:
            raise AurumError("Stored mixed electronic checkout is inconsistent") from error
        receipt = build_mixed_receipt(result)
        if (
            marker.payment_journal_version != 3
            or marker.cash_intent_id is not None
            or marker.payment_plan_intent_id != saved.operation_id
            or marker.id != saved.operation_id
            or marker.sale_id != sale.id
            or marker.tenant_id != sale.tenant_id
            or marker.request_hash != payment_intent_request_hash(saved.plan.request)
            or (
                result.tenant_id,
                result.sale_id,
                result.branch_id,
                result.register_id,
                result.shift_id,
                result.cashier_id,
                result.operation_id,
                result.stock_command_id,
            )
            != (
                sale.tenant_id,
                sale.id,
                sale.branch_id,
                sale.register_id,
                sale.shift_id,
                sale.cashier_user_id,
                marker.id,
                marker.id,
            )
            or result.total_amount != sale.total_amount
            or result.total_amount != saved.plan.quote.total_amount
            or (
                result.receipt_number,
                result.receipt_sequence,
                result.completed_at,
                result.created_at,
            )
            != (sale.receipt_number, sale.receipt_seq, sale.completed_at, sale.created_at)
            or sale.status != "completed"
            or sale.is_test
            or result.is_test
            or receipt.model_dump(mode="json") != marker.receipt_payload
            or receipt.model_dump(mode="json") != sale.receipt_snapshot
            or tuple(line.model_dump(exclude={"name"}) for line in result.lines)
            != tuple(line.model_dump() for line in saved.plan.quote.lines)
        ):
            raise AurumError("Stored mixed electronic checkout is inconsistent")
        attempts = await self._attempts._attempts(saved, sale, status="consumed", lock=False)
        if result.payments != self._attempts._payments(saved, attempts) or any(
            attempt.consumed_at != result.completed_at for attempt in attempts
        ):
            raise AurumError("Stored mixed electronic payment attempts are inconsistent")
        actual = await self._electronic.payments(sale.tenant_id, sale.id)
        expected = {part.id: part for part in result.payments}
        if len(actual) != len(expected) or any(
            payment.id not in expected
            or payment.tenant_id != sale.tenant_id
            or payment.sale_id != sale.id
            or payment.payment_method != expected[payment.id].method
            or payment.amount != expected[payment.id].amount
            or payment.currency != "TJS"
            or not self._attempts._payment_operation_matches(payment, marker.id)
            or payment.operation_hash != marker.request_hash
            or payment.payment_attempt_id
            != (None if payment.payment_method == "cash" else payment.id)
            or payment.metadata_json is not None
            for payment in actual
        ):
            raise AurumError("Stored mixed electronic payments are inconsistent")
        await UnitCheckoutPaymentService(
            UnitCheckoutPaymentRepository(self.repository.session)
        ).validate_saved(3, result)
        await self.cart.assert_actor(sale.tenant_id, sale.cashier_user_id, sale.branch_id)
        return result
