"""Recover a cashier's immutable mixed payout without posting or re-quoting."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_cash_refund_schemas import (
    MixedCashRefundReceiptV4,
    MixedCashRefundResultV4,
)
from app.domains.pos.mixed_cash_refund_service import mixed_cash_refund_fingerprint
from app.domains.pos.mixed_cash_refund_status_repository import (
    MixedCashRefundStatusRepository,
    MixedCashRefundStatusSnapshot,
)
from app.domains.pos.mixed_cash_refund_status_schemas import (
    MixedRefundOperationCompleted,
    MixedRefundOperationNotFound,
    MixedRefundOperationRead,
)
from app.domains.pos.mixed_documents import MixedCheckoutResultV4, build_mixed_receipt
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection, MixedReturnSelection
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_schemas import MixedReturnJournalResult
from app.domains.pos.mixed_return_journal_service import mixed_return_fingerprint
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_contracts import CheckoutPaymentV2


def _original(snapshot: MixedCashRefundStatusSnapshot) -> MixedCheckoutResultV4:
    parent, checkout = snapshot.parent, snapshot.checkout
    if checkout is None or parent.status != "completed" or parent.sale_type != "sale":
        raise BusinessRuleError("Completed original mixed sale required")
    try:
        original = MixedCheckoutResultV4.model_validate(checkout.result_payload)
    except ValidationError as error:
        raise AurumError("Stored original mixed sale is inconsistent") from error
    receipt = build_mixed_receipt(original)
    if (
        parent.is_test
        or parent.parent_sale_id is not None
        or original.is_test
        or checkout.tenant_id != parent.tenant_id
        or checkout.sale_id != parent.id
        or checkout.branch_id != parent.branch_id
        or checkout.register_id != parent.register_id
        or checkout.shift_id != parent.shift_id
        or checkout.cashier_user_id != parent.cashier_user_id
        or checkout.cash_intent_id != checkout.id
        or checkout.stock_command_id != checkout.id
        or checkout.payment_id != checkout.id
        or original.tenant_id != parent.tenant_id
        or original.sale_id != parent.id
        or original.operation_id != checkout.id
        or original.event_id != checkout.id
        or original.branch_id != parent.branch_id
        or original.register_id != parent.register_id
        or original.shift_id != parent.shift_id
        or original.cashier_id != parent.cashier_user_id
        or original.stock_command_id != checkout.stock_command_id
        or original.total_amount != parent.total_amount
        or original.currency != parent.currency
        or original.receipt_number != parent.receipt_number
        or original.receipt_sequence != parent.receipt_seq
        or original.completed_at != parent.completed_at
        or original.created_at != parent.created_at
        or original.payments
        != (CheckoutPaymentV2(id=checkout.id, method="cash", amount=original.total_amount),)
        or receipt.model_dump(mode="json") != parent.receipt_snapshot
        or receipt.model_dump(mode="json") != checkout.receipt_payload
    ):
        raise AurumError("Stored original mixed sale is inconsistent")
    return original


def _receipt_lines_match(
    receipt: MixedCashRefundReceiptV4,
    received: MixedReturnJournalResult,
    original: MixedCheckoutResultV4,
) -> bool:
    source = {line.id: line for line in original.lines}
    if len(receipt.lines) != len(received.lines):
        return False
    for line, allocation in zip(receipt.lines, received.lines, strict=True):
        historical = source.get(allocation.line_id)
        if historical is None or (
            line.original_line_id != allocation.line_id
            or line.original_position != allocation.position
            or line.original_position != historical.position
            or line.inventory_kind != allocation.inventory_kind
            or line.inventory_kind != historical.inventory_kind
            or line.name != historical.name
            or line.original_retail_unit_price != historical.original_retail_unit_price
            or line.quantity != allocation.quantity
            or line.offset != allocation.offset
            or line.net_amount != allocation.net_amount
            or line.discount_amount != allocation.discount_amount
            or line.gross_amount != allocation.gross_amount
        ):
            return False
        if line.inventory_kind == "physical_unit":
            if (
                allocation.inventory_kind != "physical_unit"
                or historical.inventory_kind != "physical_unit"
                or line.sale_format != allocation.sale_format
                or line.base_units != allocation.base_units
                or line.packaging != historical.packaging
                or line.original_sale_format != historical.sale_format
            ):
                return False
    return True


class MixedCashRefundStatusService:
    def __init__(self, repository: MixedCashRefundStatusRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        parent_sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> MixedRefundOperationRead:
        authority = MixedReturnJournalRepository(self.repository.session)
        await authority.assert_identity(tenant_id, actor_id)
        snapshot = await self.repository.read(tenant_id, parent_sale_id, operation_id)
        if snapshot is None:
            raise NotFoundError("Mixed refund parent sale not found")
        parent, marker = snapshot.parent, snapshot.marker
        if allowed_branch_ids is not None and parent.branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Mixed refund branch access denied")
        await authority.assert_actor(tenant_id, actor_id, parent.branch_id)
        if parent.tenant_id != tenant_id or parent.id != parent_sale_id:
            raise AurumError("Stored original mixed sale is inconsistent")
        original = _original(snapshot)
        if marker is None or marker.actor_id != actor_id:
            return MixedRefundOperationNotFound(
                operation_id=operation_id, parent_sale_id=parent_sale_id
            )
        sale, payment, journal = snapshot.returned_sale, snapshot.payment, snapshot.journal
        checkout = snapshot.checkout
        if sale is None or payment is None or journal is None or checkout is None:
            raise AurumError("Stored mixed refund is inconsistent")
        try:
            result = MixedCashRefundResultV4.model_validate(marker.result_payload)
            receipt = MixedCashRefundReceiptV4.model_validate(marker.receipt_payload)
            received = MixedReturnJournalResult.model_validate(journal.result_payload)
        except ValidationError as error:
            raise AurumError("Stored mixed refund is inconsistent") from error
        selections: tuple[MixedReturnSelection, ...] = tuple(
            LegacyReturnSelection(line.line_id, line.quantity)
            if line.inventory_kind == "legacy_pack"
            else PhysicalReturnSelection(line.line_id, line.sale_format, line.quantity)
            for line in received.lines
        )
        try:
            fingerprint = mixed_cash_refund_fingerprint(
                tenant_id,
                actor_id,
                parent_sale_id,
                marker.shift_id,
                selections,
                reason_code=marker.reason_code,
                comment=marker.comment,
                review_hash=marker.review_hash,
            )
        except BusinessRuleError as error:
            raise AurumError("Stored mixed refund is inconsistent") from error
        if (
            marker.tenant_id != tenant_id
            or marker.parent_sale_id != parent_sale_id
            or marker.id != operation_id
            or marker.branch_id != parent.branch_id
            or marker.register_id != parent.register_id
            or marker.payment_id != operation_id
            or marker.request_hash != fingerprint
            or result.operation_id != operation_id
            or result.parent_sale_id != parent_sale_id
            or result.tenant_id != tenant_id
            or result.sale_id != sale.id
            or result.sale_id != marker.sale_id
            or result.branch_id != marker.branch_id
            or result.register_id != marker.register_id
            or result.shift_id != marker.shift_id
            or result.actor_id != actor_id
            or result.payment_id != marker.payment_id
            or sale.tenant_id != tenant_id
            or sale.branch_id != marker.branch_id
            or sale.register_id != marker.register_id
            or sale.shift_id != marker.shift_id
            or sale.cashier_user_id != actor_id
            or sale.operation_id != operation_id
            or sale.operation_hash != marker.request_hash
            or sale.status != "completed"
            or sale.sale_type != "return"
            or sale.parent_sale_id != parent_sale_id
            or sale.is_test
            or sale.currency != "TJS"
            or sale.total_amount != result.total_amount
            or sale.receipt_number != receipt.receipt_number
            or sale.receipt_seq != receipt.receipt_sequence
            or sale.completed_at != receipt.completed_at
            or sale.receipt_snapshot != receipt.model_dump(mode="json")
            or receipt.parent_receipt_number != parent.receipt_number
            or receipt != result.receipt
            or payment.tenant_id != tenant_id
            or payment.id != marker.payment_id
            or payment.sale_id != sale.id
            or payment.payment_method != "cash"
            or payment.amount != result.total_amount
            or payment.currency != "TJS"
            or payment.operation_id is not None
            or payment.operation_hash is not None
            or payment.metadata_json is not None
            or journal.tenant_id != tenant_id
            or journal.id != operation_id
            or journal.parent_sale_id != parent_sale_id
            or journal.parent_checkout_id != checkout.id
            or journal.stock_command_id != checkout.stock_command_id
            or journal.branch_id != parent.branch_id
            or journal.actor_id != actor_id
            or journal.state != "prepared_quarantine"
            or journal.request_hash
            != mixed_return_fingerprint(tenant_id, actor_id, parent_sale_id, selections)
            or journal.total_amount != result.total_amount
            or journal.physical_cost_amount != result.physical_cost_amount
            or received.operation_id != operation_id
            or received.parent_sale_id != parent_sale_id
            or received.tenant_id != tenant_id
            or received.branch_id != parent.branch_id
            or received.actor_id != actor_id
            or received.total_amount != result.total_amount
            or received.physical_cost_amount != result.physical_cost_amount
            or not _receipt_lines_match(receipt, received, original)
        ):
            raise AurumError("Stored mixed refund is inconsistent")
        await authority.assert_actor(tenant_id, actor_id, parent.branch_id)
        return MixedRefundOperationCompleted(
            operation_id=operation_id,
            parent_sale_id=parent_sale_id,
            sale_id=result.sale_id,
            total_amount=result.total_amount,
            receipt=receipt,
        )
