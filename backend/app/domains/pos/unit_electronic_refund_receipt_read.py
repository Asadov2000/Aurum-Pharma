"""Read saved electronic return retail facts without payout-journal or cost access."""

from pydantic import ValidationError

from app.core.errors import AurumError
from app.domains.pos.models import Sale
from app.domains.pos.unit_electronic_refund_posting_schemas import PhysicalElectronicRefundReceiptV3


def electronic_refund_snapshot(sale: Sale) -> PhysicalElectronicRefundReceiptV3 | None:
    """Recognize our document and bind its historical facts to the authorized sale."""
    payload = sale.receipt_snapshot
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise AurumError("Receipt snapshot is invalid")
    if payload.get("document_kind") != "physical_electronic_refund_receipt":
        return None
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 3:
        raise AurumError("Electronic refund receipt version is unsupported")
    try:
        receipt = PhysicalElectronicRefundReceiptV3.model_validate(payload)
    except ValidationError as exc:
        raise AurumError("Electronic refund receipt snapshot is invalid") from exc
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
        raise AurumError("Electronic refund receipt snapshot does not match the sale")
    return receipt
