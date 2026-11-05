"""Pure validation for immutable posted electronic receipts and historical money."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domains.pos.unit_electronic_refund_posting_schemas import (
    ElectronicRefundPaymentV3,
    PhysicalElectronicRefundReceiptV3,
    PostedElectronicRefund,
)


def payment(method: str = "card") -> ElectronicRefundPaymentV3:
    return ElectronicRefundPaymentV3.model_validate(
        {
            "id": uuid4(),
            "original_payment_id": uuid4(),
            "method": method,
            "amount": "3.99",
            "terminal_id": None if method == "cash" else "test-terminal",
            "document_number": None if method == "cash" else "test-document",
        }
    )


def receipt() -> PhysicalElectronicRefundReceiptV3:
    return PhysicalElectronicRefundReceiptV3.model_validate(
        {
            "parent_sale_id": uuid4(),
            "parent_receipt_number": "R-1",
            "receipt_number": "R-2",
            "receipt_sequence": 2,
            "completed_at": datetime(2026, 10, 19, 11, tzinfo=UTC),
            "total_amount": "3.99",
            "lines": [
                {
                    "original_line_id": uuid4(),
                    "position": 1,
                    "name": "Synthetic medicine",
                    "packaging": {
                        "unit": "tablet",
                        "units_per_pack": 30,
                        "units_per_blister": 10,
                        "dispense_step": 1,
                    },
                    "original_sale_format": "pack",
                    "original_retail_unit_price": "60.00",
                    "sale_format": "unit",
                    "quantity": 2,
                    "base_units": 2,
                    "offset": 0,
                    "gross_amount": "4.00",
                    "discount_amount": "0.01",
                    "net_amount": "3.99",
                }
            ],
            "payments": [payment()],
        }
    )


def posted() -> PostedElectronicRefund:
    saved = receipt()
    return PostedElectronicRefund(
        operation_id=uuid4(),
        parent_sale_id=saved.parent_sale_id,
        sale_id=uuid4(),
        tenant_id=uuid4(),
        branch_id=uuid4(),
        register_id=uuid4(),
        shift_id=uuid4(),
        actor_id=uuid4(),
        total_amount=saved.total_amount,
        total_cost_amount=Decimal("1.00"),
        receipt=saved,
    )


def test_historical_receipt_roundtrips_json_and_remains_immutable() -> None:
    result = posted()
    assert PostedElectronicRefund.model_validate_json(result.model_dump_json()) == result
    assert result.receipt.schema_version == 3
    assert result.receipt.lines[0].base_units == 2
    assert result.receipt.total_amount == Decimal("3.99")
    with pytest.raises(ValidationError):
        result.total_amount = Decimal("2.00")


def test_cash_component_has_no_terminal_evidence() -> None:
    cash = payment("cash")
    assert cash.terminal_id is None and cash.document_number is None
    values = cash.model_dump()
    values["terminal_id"] = "not-cash-evidence"
    with pytest.raises(ValidationError):
        ElectronicRefundPaymentV3.model_validate(values)


@pytest.mark.parametrize("field", ["terminal_id", "document_number"])
@pytest.mark.parametrize("value", [None, " ", "bad\nvalue"])
def test_electronic_payment_requires_complete_safe_evidence(field: str, value: str | None) -> None:
    values = payment().model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        ElectronicRefundPaymentV3.model_validate(values)


@pytest.mark.parametrize("field,limit", [("terminal_id", 64), ("document_number", 128)])
def test_terminal_evidence_rejects_noncanonical_and_oversized_values(
    field: str, limit: int
) -> None:
    values = payment("qr").model_dump()
    values[field] = "x" * limit
    assert getattr(ElectronicRefundPaymentV3.model_validate(values), field) == "x" * limit
    for invalid in ("x" * (limit + 1), " padded "):
        values[field] = invalid
        with pytest.raises(ValidationError):
            ElectronicRefundPaymentV3.model_validate(values)


@pytest.mark.parametrize("amount", ["0.00", "-0.01", "1.001"])
def test_component_amount_requires_positive_whole_kopecks(amount: str) -> None:
    values = payment().model_dump()
    values["amount"] = amount
    with pytest.raises(ValidationError):
        ElectronicRefundPaymentV3.model_validate(values)


@pytest.mark.parametrize("identity", ["id", "original_payment_id"])
def test_distinct_receipt_payments_cannot_alias_identity(identity: str) -> None:
    values = receipt().model_dump()
    first = payment().model_dump()
    second = payment("qr").model_dump()
    first["amount"], second["amount"] = "2.00", "1.99"
    second[identity] = first[identity]
    values["payments"] = [first, second]
    with pytest.raises(ValidationError):
        PhysicalElectronicRefundReceiptV3.model_validate(values)


def test_cash_only_receipt_cannot_claim_electronic_posting() -> None:
    values = receipt().model_dump()
    values["payments"] = [payment("cash")]
    with pytest.raises(ValidationError):
        PhysicalElectronicRefundReceiptV3.model_validate(values)


@pytest.mark.parametrize("change", ["receipt_total", "payment_total", "line_balance", "position"])
def test_receipt_requires_balanced_allocation_and_ordered_lines(change: str) -> None:
    values = receipt().model_dump()
    if change == "receipt_total":
        values["total_amount"] = "4.00"
    elif change == "payment_total":
        values["payments"][0]["amount"] = "4.00"
    elif change == "line_balance":
        values["lines"][0]["gross_amount"] = "4.01"
    else:
        values["lines"][0]["position"] = 2
    with pytest.raises(ValidationError):
        PhysicalElectronicRefundReceiptV3.model_validate(values)


@pytest.mark.parametrize("quantity", [True, 1.5, "2", 0])
def test_v3_line_quantity_is_strict_positive_whole_quantity(quantity: object) -> None:
    values = receipt().model_dump()
    values["lines"][0]["quantity"] = quantity
    with pytest.raises(ValidationError):
        PhysicalElectronicRefundReceiptV3.model_validate(values)


@pytest.mark.parametrize("field", ["parent_sale_id", "total_amount"])
def test_posted_result_must_match_historical_receipt_scope(field: str) -> None:
    values = posted().model_dump()
    values[field] = uuid4() if field == "parent_sale_id" else "4.00"
    with pytest.raises(ValidationError):
        PostedElectronicRefund.model_validate(values)


def test_receipt_rejects_test_mode_and_wrong_document_version() -> None:
    values = receipt().model_dump()
    for field, value in (("is_test", True), ("schema_version", 2)):
        changed = dict(values)
        changed[field] = value
        with pytest.raises(ValidationError):
            PhysicalElectronicRefundReceiptV3.model_validate(changed)


def test_posted_return_must_have_distinct_sale_identity() -> None:
    values = posted().model_dump()
    values["sale_id"] = values["parent_sale_id"]
    with pytest.raises(ValidationError):
        PostedElectronicRefund.model_validate(values)
