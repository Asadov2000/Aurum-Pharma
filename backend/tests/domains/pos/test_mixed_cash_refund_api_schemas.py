"""Public requests distinguish fractional packs from whole physical units."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.domains.pos.mixed_cash_refund_api_schemas import (
    MixedRefundCompleteRequest,
    MixedRefundQuoteRequest,
)
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection


def legacy_item(**changes: object) -> dict[str, object]:
    return {
        "inventory_kind": "legacy_pack",
        "line_id": UUID(int=40),
        "quantity": "0.001",
        **changes,
    }


def physical_item(**changes: object) -> dict[str, object]:
    return {
        "inventory_kind": "physical_unit",
        "line_id": UUID(int=20),
        "sale_format": "unit",
        "quantity": 1,
        **changes,
    }


def complete_payload(**changes: object) -> dict[str, object]:
    return {
        "items": [legacy_item(), physical_item()],
        "operation_id": uuid4(),
        "shift_id": UUID(int=4),
        "expected_total_amount": "0.01",
        "expected_revision_hash": "a" * 64,
        **changes,
    }


def test_typed_selections_and_decimal_json_are_preserved() -> None:
    request = MixedRefundQuoteRequest.model_validate(
        {"items": [physical_item(sale_format="blister"), legacy_item()]}
    )
    assert request.selections() == (
        PhysicalReturnSelection(UUID(int=20), "blister", 1),
        LegacyReturnSelection(UUID(int=40), Decimal("0.001")),
    )
    payload = request.model_dump(mode="json")
    assert payload["items"][1]["quantity"] == "0.001"
    assert MixedRefundQuoteRequest.model_validate(payload) == request


@pytest.mark.parametrize("quantity", [0, -1, True, 0.001, "0.0001", "NaN", "Infinity"])
def test_legacy_rejects_invalid_or_inexact_quantities(quantity: object) -> None:
    with pytest.raises(ValidationError):
        MixedRefundQuoteRequest.model_validate({"items": [legacy_item(quantity=quantity)]})


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "1", "1.001"])
def test_physical_requires_positive_whole_integer(quantity: object) -> None:
    with pytest.raises(ValidationError):
        MixedRefundQuoteRequest.model_validate({"items": [physical_item(quantity=quantity)]})


@pytest.mark.parametrize(
    "items",
    [
        [],
        [legacy_item()] * 201,
        [legacy_item(), legacy_item()],
        [legacy_item(), physical_item(line_id=UUID(int=40))],
        [{"line_id": UUID(int=40), "quantity": "0.001"}],
        [legacy_item(inventory_kind="unknown")],
        [legacy_item(sale_format="unit")],
        [physical_item(sale_format="unknown")],
        [legacy_item(cost_amount="0.01")],
        [physical_item(total_cost_amount="0.01")],
    ],
)
def test_ambiguous_duplicate_and_private_fields_are_rejected(items: list[object]) -> None:
    with pytest.raises(ValidationError):
        MixedRefundQuoteRequest.model_validate({"items": items})


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": UUID(int=1)},
        {"operation_id": None},
        {"shift_id": None},
        {"expected_total_amount": 0.01},
        {"expected_revision_hash": "A" * 64},
        {"expected_revision_hash": "a" * 63},
        {"expected_revision_hash": None},
        {"reason_code": "unknown"},
        {"comment": "a" * 501},
        {"comment": True},
        {"tenant_id": UUID(int=1)},
        {"actor_id": UUID(int=2)},
    ],
)
def test_completion_requires_bound_identity_review_and_reason(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MixedRefundCompleteRequest.model_validate(complete_payload(**changes))


@pytest.mark.parametrize(
    "field", ["operation_id", "shift_id", "expected_total_amount", "expected_revision_hash"]
)
def test_completion_review_fields_cannot_be_omitted(field: str) -> None:
    payload = complete_payload()
    del payload[field]
    with pytest.raises(ValidationError):
        MixedRefundCompleteRequest.model_validate(payload)


def test_completion_round_trip_preserves_reason_and_comment() -> None:
    request = MixedRefundCompleteRequest.model_validate(
        complete_payload(reason_code="other", comment="Причина возврата")
    )
    assert request.reason_code == "other"
    assert MixedRefundCompleteRequest.model_validate(request.model_dump(mode="json")) == request
