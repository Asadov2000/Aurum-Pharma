"""Zero intake exposes discriminated quantities without financial or cost fields."""

from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.domains.pos.mixed_zero_return_schemas import (
    MixedZeroReturnRead,
    MixedZeroReturnRequest,
    MixedZeroReturnResult,
)


def result() -> dict[str, object]:
    return {
        "operation_id": str(uuid4()),
        "parent_sale_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "branch_id": str(uuid4()),
        "actor_id": str(uuid4()),
        "total_amount": "0.00",
        "lines": [
            {
                "inventory_kind": "legacy_pack",
                "line_id": str(uuid4()),
                "quantity": "0.001",
                "offset": "0.002",
            },
            {
                "inventory_kind": "physical_unit",
                "line_id": str(uuid4()),
                "sale_format": "blister",
                "quantity": 1,
                "base_units": 10,
                "offset": 20,
            },
        ],
    }


def test_acknowledgment_roundtrip_and_discriminated_not_found() -> None:
    acknowledgment = MixedZeroReturnResult.model_validate(result())
    adapter = TypeAdapter(MixedZeroReturnRead)
    assert adapter.validate_json(acknowledgment.model_dump_json()) == acknowledgment
    assert acknowledgment.model_dump(mode="json")["total_amount"] == "0.00"
    missing = adapter.validate_python(
        {"state": "not_found", "parent_sale_id": str(uuid4()), "operation_id": str(uuid4())}
    )
    assert missing.state == "not_found"


@pytest.mark.parametrize("field", ["receipt", "sale_id", "payment_id", "cost_amount", "shift_id"])
def test_acknowledgment_rejects_nonpublic_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        MixedZeroReturnResult.model_validate({**result(), field: "forged"})


@pytest.mark.parametrize("amount", ["0.01", "-0.01", True, "NaN"])
def test_acknowledgment_requires_exact_zero(amount: object) -> None:
    with pytest.raises(ValidationError):
        MixedZeroReturnResult.model_validate({**result(), "total_amount": amount})


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", True),
        ("quantity", "0.0001"),
        ("quantity", "0.000"),
        ("offset", "-0.001"),
        ("offset", "0.0001"),
        ("inventory_kind", "physical_unit"),
        ("cost_amount", "0.00"),
    ],
)
def test_acknowledgment_rejects_malformed_legacy_fields(field: str, value: object) -> None:
    payload = result()
    acknowledgment = MixedZeroReturnResult.model_validate(payload)
    lines = [line.model_dump(mode="json") for line in acknowledgment.lines]
    lines[0][field] = value
    with pytest.raises(ValidationError):
        MixedZeroReturnResult.model_validate({**payload, "lines": lines})


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", True),
        ("quantity", "1"),
        ("quantity", 1.5),
        ("base_units", 0),
        ("offset", -1),
        ("cost_amount", "0.00"),
    ],
)
def test_acknowledgment_rejects_malformed_physical_fields(field: str, value: object) -> None:
    payload = result()
    acknowledgment = MixedZeroReturnResult.model_validate(payload)
    lines = [line.model_dump(mode="json") for line in acknowledgment.lines]
    lines[1][field] = value
    with pytest.raises(ValidationError):
        MixedZeroReturnResult.model_validate({**payload, "lines": lines})


@pytest.mark.parametrize("field", ["shift_id", "expected_total_amount", "cost_amount"])
def test_zero_request_cannot_masquerade_as_payout(field: str) -> None:
    payload = {
        "operation_id": str(uuid4()),
        "expected_revision_hash": "a" * 64,
        "items": [
            {
                "inventory_kind": "physical_unit",
                "line_id": str(UUID(int=1)),
                "sale_format": "unit",
                "quantity": 1,
            }
        ],
        field: "forged",
    }
    with pytest.raises(ValidationError):
        MixedZeroReturnRequest.model_validate(payload)
