"""Read authorized cash return receipts without refund-journal or cost access."""

from decimal import Decimal

from pydantic import ValidationError

from app.core.errors import AurumError
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundReceiptV3
from app.domains.pos.schemas import ReceiptLine
from app.domains.pos.unit_documents import UNIT_LABELS
from app.domains.pos.unit_electronic_refund_posting_schemas import PhysicalElectronicRefundReceiptV3


def physical_cash_refund_snapshot(sale: Sale) -> PhysicalCashRefundReceiptV3 | None:
    """Recognize our document and bind its historical facts to the authorized sale."""
    payload = sale.receipt_snapshot
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise AurumError("Receipt snapshot is invalid")
    if payload.get("document_kind") != "physical_cash_refund_receipt":
        return None
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 3:
        raise AurumError("Physical refund receipt version is unsupported")
    try:
        receipt = PhysicalCashRefundReceiptV3.model_validate(payload)
    except ValidationError as exc:
        raise AurumError("Physical refund receipt snapshot is invalid") from exc
    if (
        sale.status != "completed"
        or sale.sale_type != "return"
        or receipt.parent_sale_id != sale.parent_sale_id
        or receipt.receipt_number != sale.receipt_number
        or receipt.receipt_sequence != sale.receipt_seq
        or receipt.completed_at != sale.completed_at
        or receipt.is_test != sale.is_test
        or receipt.currency != sale.currency
        or receipt.total_amount != sale.total_amount
    ):
        raise AurumError("Physical refund receipt snapshot does not match the sale")
    return receipt


def physical_cash_refund_lines(
    receipt: PhysicalCashRefundReceiptV3 | PhysicalElectronicRefundReceiptV3,
) -> list[ReceiptLine]:
    """Display each exact allocation without inventing a rounded per-unit price.

    One line is one returned allocation. Its actual physical quantity and the
    original price format remain explicit in the description. A division by the
    returned quantity could invent a price whose rounded product differs from
    the historical allocated amount.
    """
    lines: list[ReceiptLine] = []
    for item in receipt.lines:
        labels = {"pack": "уп.", "blister": "блист.", "unit": UNIT_LABELS[item.packaging.unit]}
        description = (
            f"Возвращено: {item.quantity} {labels[item.sale_format]}; "
            f"исходная цена: {item.original_retail_unit_price:.2f} TJS / "
            f"{labels[item.original_sale_format]}"
        )
        lines.append(
            ReceiptLine(
                position=item.position,
                name=item.name,
                qty=Decimal("1"),
                unit_price=item.gross_amount,
                discount_amount=item.discount_amount,
                total_price=item.net_amount,
                unit_label="возврат",
                quantity_description=description,
            )
        )
    return lines
