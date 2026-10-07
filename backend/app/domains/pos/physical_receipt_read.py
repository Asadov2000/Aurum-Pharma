"""Read-only adaptation of immutable physical receipts; no inventory or posting."""

from decimal import Decimal

from pydantic import ValidationError

from app.core.errors import AurumError
from app.domains.pos.models import Sale
from app.domains.pos.physical_documents import PhysicalReceiptDocumentV3
from app.domains.pos.schemas import ReceiptLine
from app.domains.pos.unit_documents import UNIT_LABELS


def physical_receipt_snapshot(sale: Sale) -> PhysicalReceiptDocumentV3 | None:
    """Recognize versioned snapshots and bind their saved facts to the authorized sale."""
    payload = sale.receipt_snapshot
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise AurumError("Receipt snapshot is invalid")
    if not ({"schema_version", "document_kind"} & payload.keys()):
        return None
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 3
        or payload.get("document_kind") != "physical_receipt"
    ):
        raise AurumError("Receipt snapshot version is unsupported")
    try:
        receipt = PhysicalReceiptDocumentV3.model_validate(payload)
    except ValidationError as exc:
        raise AurumError("Physical receipt snapshot is invalid") from exc
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
        raise AurumError("Physical receipt snapshot does not match the sale")
    return receipt


def physical_receipt_lines(receipt: PhysicalReceiptDocumentV3) -> list[ReceiptLine]:
    """Keep the historical format quantity and its own retail price together."""
    lines: list[ReceiptLine] = []
    for item in receipt.lines:
        unit_label = UNIT_LABELS[item.packaging.unit]
        description = None
        if item.sale_format == "pack":
            description = f"В упаковке: {item.packaging.units_per_pack} {unit_label}"
        elif item.sale_format == "blister":
            description = f"В блистере: {item.packaging.units_per_blister} {unit_label}"
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
