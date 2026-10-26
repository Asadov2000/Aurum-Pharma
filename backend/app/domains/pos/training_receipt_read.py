"""Read-only display adapter for immutable, explicitly marked training receipts."""

import json
from decimal import Decimal

from pydantic import ValidationError

from app.core.errors import AurumError
from app.domains.pos.models import Sale
from app.domains.pos.physical_documents import PhysicalReceiptLineV3
from app.domains.pos.schemas import ReceiptLine
from app.domains.pos.training_checkout_schemas import TrainingReceiptDocumentV1
from app.domains.pos.unit_documents import UNIT_LABELS


def training_receipt_snapshot(sale: Sale) -> TrainingReceiptDocumentV1 | None:
    payload = sale.receipt_snapshot
    if not isinstance(payload, dict) or payload.get("document_kind") != "training_sale_receipt":
        return None
    try:
        receipt = TrainingReceiptDocumentV1.model_validate_json(
            json.dumps(payload, ensure_ascii=False, allow_nan=False)
        )
    except ValidationError as error:
        raise AurumError("Training receipt snapshot is invalid") from error
    if (
        sale.is_test is not True
        or sale.status != "completed"
        or sale.sale_type != "sale"
        or sale.id != receipt.sale_id
        or sale.tenant_id != receipt.tenant_id
        or sale.branch_id != receipt.branch_id
        or sale.register_id != receipt.register_id
        or sale.shift_id != receipt.shift_id
        or sale.cashier_user_id != receipt.cashier_id
        or sale.receipt_number != receipt.receipt_number
        or sale.receipt_seq != receipt.receipt_sequence
        or sale.completed_at != receipt.completed_at
        or sale.total_amount != receipt.total_amount
        or sale.currency != receipt.currency
    ):
        raise AurumError("Training receipt snapshot does not match the saved test sale")
    return receipt


def training_receipt_lines(receipt: TrainingReceiptDocumentV1) -> list[ReceiptLine]:
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
