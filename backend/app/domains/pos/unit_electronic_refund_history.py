"""Validate immutable source payments and reconcile goods with actual payouts."""

from decimal import Decimal, localcontext
from uuid import UUID

from app.core.errors import AurumError, BusinessRuleError
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3, build_physical_receipt
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.pos.unit_electronic_refund_preview_schemas import ElectronicRefundHistory
from app.domains.pos.unit_money import allocate_unit_amount


def original_electronic_checkout(
    history: ElectronicRefundHistory, tenant_id: UUID, parent_sale_id: UUID
) -> PhysicalCheckoutResultV3:
    sale, marker = history.sale, history.checkout
    original = marker.result_payload
    if marker.payment_journal_version != 3:
        raise BusinessRuleError("Electronic refund review requires an electronic checkout")
    receipt = build_physical_receipt(original)
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
    history: ElectronicRefundHistory, original: PhysicalCheckoutResultV3
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
            or (component.tenant_id, component.sale_id)
            != (original.tenant_id, original.sale_id)
            or component.physical_checkout_id != original.operation_id
            or component.mixed_checkout_id is not None
            or (component.method, component.amount, component.currency)
            != (part.method, part.amount, "TJS")
            or component.payment_attempt_id != attempt_id
            or component.created_by != original.cashier_id
        ):
            raise AurumError("Original electronic payment facts are inconsistent")
    if len({payment.operation_id for payment in history.payments}) != len(expected):
        raise AurumError("Original electronic operation identifiers are inconsistent")


def returned_goods(
    history: ElectronicRefundHistory, original: PhysicalCheckoutResultV3
) -> dict[UUID, int]:
    sources = {line.id: line for line in original.lines}
    cursors: dict[UUID, int] = {}
    for row in sorted(history.return_lines, key=lambda row: (str(row.line_id), row.offset)):
        source = sources.get(row.line_id)
        if (
            source is None
            or (row.tenant_id, row.branch_id, row.stock_command_id)
            != (original.tenant_id, original.branch_id, original.stock_command_id)
            or row.offset != cursors.get(row.line_id, 0)
        ):
            raise AurumError("Stored physical return history is inconsistent")
        try:
            amount = allocate_unit_amount(
                source.net_amount,
                total_units=source.base_units,
                offset=row.offset,
                quantity=row.base_units,
            ).amount
        except ValueError:
            raise AurumError("Stored physical return quantities are inconsistent") from None
        if amount != row.net_amount:
            raise AurumError("Stored physical return amounts are inconsistent")
        cursors[row.line_id] = row.offset + row.base_units
    return cursors


def returned_payments(
    history: ElectronicRefundHistory, original: PhysicalCheckoutResultV3
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
                or payout.metadata is not None
                or payout.payment_attempt_id is not None
                or not payout.amount.is_finite()
                or payout.amount <= 0
                or (component.tenant_id, component.parent_sale_id, component.sale_id)
                != (original.tenant_id, original.sale_id, sale.id)
                or component.physical_refund_id is None
                or component.mixed_refund_id is not None
                or (component.method, component.amount, component.currency)
                != (source.method, payout.amount, "TJS")
                or component.created_by != sale.cashier_user_id
            ):
                raise AurumError("Stored refund payment facts are inconsistent")
            returned[source.id] = returned.get(source.id, Decimal("0.00")) + payout.amount
            totals[sale.id] = totals.get(sale.id, Decimal("0.00")) + payout.amount
        if any(totals[sale.id] != sale.total_amount for sale in sales.values()):
            raise AurumError("Stored refund sale totals are inconsistent")
    return returned


def require_reconciled_history(
    original: PhysicalCheckoutResultV3,
    goods: dict[UUID, int],
    payments: dict[UUID, Decimal],
) -> None:
    with localcontext(EXACT_CONTEXT):
        goods_total = sum(
            (
                allocate_unit_amount(
                    line.net_amount,
                    total_units=line.base_units,
                    offset=0,
                    quantity=goods[line.id],
                ).amount
                for line in original.lines
                if goods.get(line.id, 0)
            ),
            Decimal("0.00"),
        )
        if any(
            payments.get(part.id, Decimal("0.00")) > part.amount for part in original.payments
        ) or goods_total != sum(payments.values(), Decimal("0.00")):
            raise AurumError("Stored goods and payment returns require reconciliation")
