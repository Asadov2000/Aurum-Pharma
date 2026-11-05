"""Validate immutable source payments and reconcile goods with actual payouts."""

from decimal import Decimal, localcontext
from uuid import UUID

from app.core.errors import AurumError, BusinessRuleError
from app.domains.pos.mixed_documents import (
    LegacyResultLineV4,
    MixedCheckoutResultV4,
    build_mixed_receipt,
)
from app.domains.pos.mixed_electronic_refund_history_schemas import MixedElectronicRefundHistory
from app.domains.pos.mixed_return_allocation import (
    LegacyReturnAllocation,
    LegacyReturnSelection,
    MixedPhysicalReturnAllocation,
    MixedReturnSelection,
    plan_mixed_return,
)
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.pos.unit_electronic_refund_history import _validate_electronic_payout_metadata


def original_electronic_checkout(
    history: MixedElectronicRefundHistory, tenant_id: UUID, parent_sale_id: UUID
) -> MixedCheckoutResultV4:
    sale, marker = history.sale, history.checkout
    original = marker.result_payload
    if marker.payment_journal_version != 3:
        raise BusinessRuleError("Electronic refund review requires an electronic checkout")
    receipt = build_mixed_receipt(original)
    if (
        sale.tenant_id != tenant_id
        or sale.id != parent_sale_id
        or sale.sale_type != "sale"
        or sale.parent_sale_id is not None
        or sale.status != "completed"
        or sale.currency != "TJS"
        or sale.is_test
        or original.is_test
        or (marker.tenant_id, marker.sale_id) != (tenant_id, parent_sale_id)
        or (original.tenant_id, original.sale_id) != (tenant_id, parent_sale_id)
        or marker.cash_intent_id is not None
        or marker.payment_plan_intent_id != marker.id
        or marker.stock_command_id != marker.id
        or marker.payment_id != marker.id
        or original.operation_id != marker.id
        or original.stock_command_id != marker.id
        or original.event_id != marker.id
        or (sale.branch_id, sale.register_id, sale.shift_id, sale.cashier_user_id)
        != (marker.branch_id, marker.register_id, marker.shift_id, marker.cashier_user_id)
        or (original.branch_id, original.register_id, original.shift_id, original.cashier_id)
        != (sale.branch_id, sale.register_id, sale.shift_id, sale.cashier_user_id)
        or original.total_amount != sale.total_amount
        or original.created_at != sale.created_at
        or original.completed_at != sale.completed_at
        or original.receipt_number != sale.receipt_number
        or original.receipt_sequence != sale.receipt_seq
        or receipt != sale.receipt_snapshot
        or receipt != marker.receipt_payload
        or not any(part.method != "cash" for part in original.payments)
    ):
        raise AurumError("Original electronic checkout is inconsistent")
    _original_payments(history, original)
    return original


def _original_payments(
    history: MixedElectronicRefundHistory, original: MixedCheckoutResultV4
) -> None:
    expected = {part.id: part for part in original.payments}
    components = {part.id: part for part in history.components}
    if (
        len(history.payments) != len(expected)
        or len(components) != len(history.components)
        or set(components) != set(expected)
        or {payment.id for payment in history.payments} != set(expected)
    ):
        raise AurumError("Original electronic payments are incomplete")
    for payment in history.payments:
        part, component = expected[payment.id], components[payment.id]
        attempt_id = None if part.method == "cash" else part.id
        if part.method == "cash":
            valid_operation = payment.operation_id == payment.id
        else:
            valid_operation = (
                payment.operation_id is not None
                and payment.operation_id.version == 4
                and payment.operation_id not in {*expected, original.operation_id}
            )
        if (
            (payment.tenant_id, payment.sale_id) != (original.tenant_id, original.sale_id)
            or (payment.payment_method, payment.amount, payment.currency)
            != (part.method, part.amount, "TJS")
            or payment.payment_attempt_id != attempt_id
            or payment.metadata is not None
            or not valid_operation
            or payment.operation_hash != history.checkout.request_hash
            or (component.tenant_id, component.sale_id) != (original.tenant_id, original.sale_id)
            or component.mixed_checkout_id != original.operation_id
            or component.physical_checkout_id is not None
            or (component.method, component.amount, component.currency)
            != (part.method, part.amount, "TJS")
            or component.payment_attempt_id != attempt_id
            or component.created_by != original.cashier_id
        ):
            raise AurumError("Original electronic payment facts are inconsistent")
    if len({payment.operation_id for payment in history.payments}) != len(expected):
        raise AurumError("Original electronic operation identifiers are inconsistent")


def returned_payments(
    history: MixedElectronicRefundHistory, original: MixedCheckoutResultV4
) -> dict[UUID, Decimal]:
    sources = {part.id: part for part in original.payments}
    sales = {sale.id: sale for sale in history.refund_sales}
    components = {part.id: part for part in history.refund_components}
    if (
        len(sales) != len(history.refund_sales)
        or len(components) != len(history.refund_components)
        or len(history.payouts) != len(components)
        or {payment.id for payment in history.payouts} != set(components)
        or {payment.sale_id for payment in history.payouts} != set(sales)
    ):
        raise AurumError("Stored refund payment history is incomplete")
    returned: dict[UUID, Decimal] = {}
    totals: dict[UUID, Decimal] = {}
    with localcontext(EXACT_CONTEXT):
        for payout in history.payouts:
            component = components[payout.id]
            sale = sales.get(payout.sale_id)
            source = sources.get(component.original_payment_id)
            if (
                sale is None
                or source is None
                or (sale.tenant_id, sale.branch_id, sale.parent_sale_id)
                != (original.tenant_id, original.branch_id, original.sale_id)
                or sale.status != "completed"
                or sale.sale_type != "return"
                or sale.currency != "TJS"
                or sale.is_test
                or payout.tenant_id != original.tenant_id
                or payout.currency != "TJS"
                or payout.payment_method != source.method
                or (
                    payout.metadata is not None
                    and component.electronic_refund_id is None
                    and component.mixed_electronic_refund_id is None
                )
                or payout.payment_attempt_id is not None
                or not payout.amount.is_finite()
                or payout.amount <= 0
                or (component.tenant_id, component.parent_sale_id, component.sale_id)
                != (original.tenant_id, original.sale_id, sale.id)
                or sum(
                    parent is not None
                    for parent in (
                        component.mixed_refund_id,
                        component.electronic_refund_id,
                        component.mixed_electronic_refund_id,
                    )
                )
                != 1
                or component.physical_refund_id is not None
                or (component.mixed_refund_id is not None and source.method != "cash")
                or (component.method, component.amount, component.currency)
                != (source.method, payout.amount, "TJS")
                or component.created_by != sale.cashier_user_id
            ):
                raise AurumError("Stored refund payment facts are inconsistent")
            if (
                component.electronic_refund_id is not None
                or component.mixed_electronic_refund_id is not None
            ):
                _validate_electronic_payout_metadata(source.id, source.method, payout.metadata)
            returned[source.id] = returned.get(source.id, Decimal("0.00")) + payout.amount
            totals[sale.id] = totals.get(sale.id, Decimal("0.00")) + payout.amount
        if any(totals[sale.id] != sale.total_amount for sale in sales.values()):
            raise AurumError("Stored refund sale totals are inconsistent")
    return returned


def returned_goods(
    history: MixedElectronicRefundHistory, original: MixedCheckoutResultV4
) -> tuple[dict[UUID, Decimal], dict[UUID, int]]:
    sources = {line.id: line for line in original.lines}
    legacy: dict[UUID, Decimal] = {}
    physical: dict[UUID, int] = {}
    for row in history.return_lines:
        source = sources.get(row.line_id)
        if (
            source is None
            or (row.tenant_id, row.branch_id, row.parent_checkout_id, row.stock_command_id)
            != (
                original.tenant_id,
                original.branch_id,
                original.operation_id,
                original.stock_command_id,
            )
            or row.catalog_id != source.catalog_id
        ):
            raise AurumError("Stored mixed return source is inconsistent")
        selection: MixedReturnSelection
        if isinstance(source, LegacyResultLineV4):
            if (
                row.inventory_kind != "legacy_pack"
                or row.batch_id != source.batch_id
                or row.legacy_offset != legacy.get(row.line_id, Decimal("0.000"))
                or row.legacy_quantity is None
                or any(
                    value is not None
                    for value in (row.offset, row.base_units, row.sale_format, row.quantity)
                )
            ):
                raise AurumError("Stored legacy return history is inconsistent")
            selection = LegacyReturnSelection(UUID(str(row.line_id)), row.legacy_quantity)
        else:
            if (
                row.inventory_kind != "physical_unit"
                or row.batch_id is not None
                or row.legacy_quantity is not None
                or row.legacy_offset is not None
                or row.offset != physical.get(row.line_id, 0)
                or row.quantity is None
                or row.sale_format not in ("pack", "blister", "unit")
            ):
                raise AurumError("Stored physical return history is inconsistent")
            selection = PhysicalReturnSelection(
                UUID(str(row.line_id)), row.sale_format, row.quantity
            )
        try:
            plan = plan_mixed_return(
                original,
                (selection,),
                returned_legacy_quantities=legacy,
                returned_base_units=physical,
            )
        except ValueError:
            raise AurumError("Stored mixed return quantity is inconsistent") from None
        line = plan.lines[0]
        if (line.net_amount, line.discount_amount, line.gross_amount) != (
            row.net_amount,
            row.discount_amount,
            row.gross_amount,
        ):
            raise AurumError("Stored mixed return amount is inconsistent")
        if isinstance(line, MixedPhysicalReturnAllocation):
            if line.base_units != row.base_units:
                raise AurumError("Stored physical return units are inconsistent")
            physical[row.line_id] = (row.offset or 0) + (row.base_units or 0)
        elif isinstance(line, LegacyReturnAllocation):
            with localcontext(EXACT_CONTEXT):
                legacy[row.line_id] = line.offset + line.quantity
    return legacy, physical


def require_reconciled_history(
    history: MixedElectronicRefundHistory,
    original: MixedCheckoutResultV4,
    payments: dict[UUID, Decimal],
) -> None:
    # Called only after every persisted goods row has matched its immutable
    # source and contiguous cumulative-floor allocation. Never synthesize a
    # payment cursor from this total: actual linked payouts must explain it.
    with localcontext(EXACT_CONTEXT):
        goods_total = sum((line.net_amount for line in history.return_lines), Decimal("0.00"))
        if any(
            payments.get(part.id, Decimal("0.00")) > part.amount for part in original.payments
        ) or goods_total != sum(payments.values(), Decimal("0.00")):
            raise AurumError("Stored mixed goods and payment returns require reconciliation")
