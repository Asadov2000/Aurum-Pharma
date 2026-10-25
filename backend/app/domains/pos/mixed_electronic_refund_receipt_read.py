"""Read saved mixed electronic refund retail facts after sale authorization."""

from app.core.errors import AurumError
from app.domains.pos.mixed_electronic_refund_posting_schemas import MixedElectronicRefundReceiptV4
from app.domains.pos.models import Sale
from app.domains.pos.physical_documents import _printable


def mixed_electronic_refund_snapshot(sale: Sale) -> MixedElectronicRefundReceiptV4 | None:
    """Bind the immutable receipt to its completed return, without private reads."""
    payload = sale.receipt_snapshot
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise AurumError("Receipt snapshot is invalid")
    if payload.get("document_kind") != "mixed_electronic_refund_receipt":
        return None
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 4:
        raise AurumError("Mixed electronic refund receipt version is unsupported")
    try:
        receipt = MixedElectronicRefundReceiptV4.model_validate(payload)
        _printable(receipt.receipt_number)
        _printable(receipt.parent_receipt_number)
        for item in receipt.lines:
            _printable(item.name)
    except ValueError as exc:
        raise AurumError("Mixed electronic refund receipt snapshot is invalid") from exc
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
        raise AurumError("Mixed electronic refund receipt snapshot does not match the sale")
    return receipt
