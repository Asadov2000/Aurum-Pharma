"""Adapt authorized immutable mixed refunds without reading private journals."""

from decimal import Decimal

from app.core.errors import AurumError
from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundReceiptV4
from app.domains.pos.models import Sale
from app.domains.pos.physical_documents import _printable
from app.domains.pos.schemas import ReceiptLine
from app.domains.pos.unit_documents import UNIT_LABELS


def mixed_cash_refund_snapshot(sale: Sale) -> MixedCashRefundReceiptV4 | None:
    """Bind saved retail facts to a return obtained through authorized sale reads."""
    payload = sale.receipt_snapshot
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise AurumError("Receipt snapshot is invalid")
    if payload.get("document_kind") != "mixed_cash_refund_receipt":
        return None
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 4:
        raise AurumError("Mixed refund receipt version is unsupported")
    try:
        receipt = MixedCashRefundReceiptV4.model_validate(payload)
        _printable(receipt.receipt_number)
        _printable(receipt.parent_receipt_number)
        for item in receipt.lines:
            _printable(item.name)
    except ValueError as exc:
        raise AurumError("Mixed refund receipt snapshot is invalid") from exc
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
        raise AurumError("Mixed refund receipt snapshot does not match the sale")
    return receipt


def mixed_cash_refund_lines(receipt: MixedCashRefundReceiptV4) -> list[ReceiptLine]:
    """Keep exact allocated money and describe the actual returned quantities.

    One display line is one allocation: dividing its historical gross by a
    fractional pack or unit count could invent a rounded price. Original prices,
    physical formats and legacy decimal pack quantities stay explicit.
    """
    lines: list[ReceiptLine] = []
    for item in receipt.lines:
        if item.inventory_kind == "legacy_pack":
            quantity = format(item.quantity, "f")
            if "." in quantity:
                quantity = quantity.rstrip("0").rstrip(".")
            description = (
                f"Возвращено: {quantity} уп.; "
                f"исходная цена: {item.original_retail_unit_price:.2f} TJS / уп."
            )
        else:
            labels = {
                "pack": "уп.",
                "blister": "блист.",
                "unit": UNIT_LABELS[item.packaging.unit],
            }
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
