"""Refund documents preserve source positions and reject ambiguous quantities."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.pos.mixed_cash_refund_review import mixed_refund_revision
from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundReceiptV4
from tests.domains.pos.test_mixed_return_allocation import (
    LEGACY_ID,
    PHYSICAL_ID,
    checkout,
    legacy_selection,
    physical_selection,
)


def receipt_payload(**changes: object) -> dict[str, object]:
    return {
        "parent_sale_id": UUID(int=1),
        "parent_receipt_number": "ORIGINAL-1",
        "receipt_number": "REFUND-2",
        "receipt_sequence": 2,
        "completed_at": datetime(2026, 10, 11, tzinfo=UTC),
        "is_test": False,
        "total_amount": "0.01",
        "lines": [
            {
                "inventory_kind": "legacy_pack",
                "original_line_id": LEGACY_ID,
                "position": 1,
                "original_position": 3,
                "name": "Историческое лекарство",
                "original_retail_unit_price": "10.00",
                "quantity": "0.001",
                "offset": "0.002",
                "gross_amount": "0.02",
                "discount_amount": "0.01",
                "net_amount": "0.01",
            }
        ],
        **changes,
    }


def test_single_kind_subset_keeps_dense_print_and_original_position() -> None:
    receipt = MixedCashRefundReceiptV4.model_validate(receipt_payload())
    assert receipt.lines[0].position == 1
    assert receipt.lines[0].original_position == 3
    assert receipt.lines[0].quantity == Decimal("0.001")
    payload = receipt.model_dump(mode="json")
    assert MixedCashRefundReceiptV4.model_validate(payload) == receipt
    assert "cost" not in receipt.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"is_test": True},
        {"is_test": 0},
        {"schema_version": True},
        {"schema_version": 3},
        {"total_amount": "0.00"},
        {"total_amount": "0.02"},
        {"total_amount": 0.01},
        {"lines": []},
        {"completed_at": "2026-10-11T00:00:00"},
        {"total_cost_amount": "0.01"},
    ],
)
def test_invalid_receipt_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MixedCashRefundReceiptV4.model_validate(receipt_payload(**changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("position", 2),
        ("original_position", 0),
        ("quantity", "0.0001"),
        ("quantity", 0.001),
        ("quantity", True),
        ("offset", "0.0001"),
        ("offset", "-0.001"),
        ("gross_amount", "0.03"),
        ("cost_amount", "0.01"),
    ],
)
def test_legacy_line_rejects_invalid_count_and_private_cost(field: str, value: object) -> None:
    original = MixedCashRefundReceiptV4.model_validate(receipt_payload())
    line = original.lines[0].model_dump(mode="json")
    line[field] = value
    with pytest.raises(ValidationError):
        MixedCashRefundReceiptV4.model_validate(receipt_payload(lines=[line]))


def test_revision_is_canonical_and_binds_unselected_cursors() -> None:
    original = checkout()
    selections = (legacy_selection("0.001"), physical_selection())
    revision = mixed_refund_revision(original, {}, {}, selections)
    assert revision == mixed_refund_revision(
        original,
        {LEGACY_ID: Decimal("0.000")},
        {PHYSICAL_ID: 0},
        (physical_selection(), legacy_selection("0.0010")),
    )
    assert revision != mixed_refund_revision(
        original, {LEGACY_ID: Decimal("0.001")}, {}, selections
    )
    assert revision != mixed_refund_revision(original, {}, {PHYSICAL_ID: 1}, selections)
    one_kind = (legacy_selection(),)
    assert mixed_refund_revision(original, {}, {}, one_kind) != mixed_refund_revision(
        original, {}, {PHYSICAL_ID: 1}, one_kind
    )
