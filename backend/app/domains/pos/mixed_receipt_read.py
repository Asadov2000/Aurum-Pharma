"""Read-only adaptation of mixed historical receipts, after sale authorization."""

from decimal import Decimal

from pydantic import ValidationError

from app.core.errors import AurumError
from app.domains.pos.mixed_documents import MixedReceiptDocumentV4
from app.domains.pos.models import Sale
from app.domains.pos.physical_documents import PhysicalReceiptLineV3
from app.domains.pos.schemas import ReceiptLine
from app.domains.pos.unit_documents import UNIT_LABELS


def mixed_receipt_snapshot(sale: Sale) -> MixedReceiptDocumentV4 | None:
    """Bind saved retail facts to a sale already obtained through authorized reads.

    Other kinds continue through the existing readers, which reject unsupported
    versioned payloads. A malformed mixed receipt never falls back to legacy.
    """
    payload = sale.receipt_snapshot
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise AurumError("Receipt snapshot is invalid")
    if payload.get("document_kind") != "mixed_receipt":
        return None
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 4:
        raise AurumError("Mixed receipt version is unsupported")
    try:
        receipt = MixedReceiptDocumentV4.model_validate(payload)
    except ValidationError as exc:
        raise AurumError("Mixed receipt snapshot is invalid") from exc
    if (
        sale.status != "completed"
        or sale.sale_type != "sale"
        or receipt.receipt_number != sale.receipt_number
        or receipt.receipt_sequence != sale.receipt_seq
        or receipt.completed_at != sale.completed_at
        or receipt.is_test != sale.is_test
        or receipt.currency != sale.currency
        or receipt.total_amount != sale.total_amount
    ):
        raise AurumError("Mixed receipt snapshot does not match the sale")
    return receipt


def mixed_receipt_lines(receipt: MixedReceiptDocumentV4) -> list[ReceiptLine]:
    """Display both kinds once, retaining their original order and quantities."""
    receipt = MixedReceiptDocumentV4.model_validate(receipt)
    lines: list[ReceiptLine] = []
    for item in receipt.lines:
        description = None
        if isinstance(item, PhysicalReceiptLineV3):
            label = UNIT_LABELS[item.packaging.unit]
            if item.sale_format == "pack":
                description = f"В упаковке: {item.packaging.units_per_pack} {label}"
            elif item.sale_format == "blister":
                description = f"В блистере: {item.packaging.units_per_blister} {label}"
        lines.append(
            ReceiptLine(
                position=item.position,
                name=item.name,
                qty=Decimal(item.quantity),
                unit_price=item.original_retail_unit_price,
                discount_amount=item.discount_amount,
                total_price=item.net_amount,
                unit_label=item.unit_label,
                quantity_description=description,
            )
        )
    return lines
