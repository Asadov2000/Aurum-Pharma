"""Pure training contracts bind saved scope, retail facts and simulated amounts."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domains.pos.training_checkout_schemas import (
    TrainingCheckoutOperationCompleted,
    TrainingCheckoutRequest,
    TrainingCheckoutResultV1,
    TrainingReceiptDocumentV1,
    training_checkout_request_hash,
    training_receipt_lines_from_quote,
)
from app.domains.pos.training_simulation import simulate_training_sale
from tests.domains.pos.test_training_simulation import (
    LEGACY,
    PHYSICAL,
    TODAY,
    legacy_line,
    legacy_stock,
    physical_line,
    physical_stock,
    quote_for,
)


def persisted_result(payload: dict[str, object]) -> TrainingCheckoutResultV1:
    return TrainingCheckoutResultV1.model_validate_json(
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    )


def saved_result(*, mixed: bool = False) -> TrainingCheckoutResultV1:
    quote = quote_for(physical_line(), legacy_line()) if mixed else quote_for(physical_line())
    simulation = simulate_training_sale(
        quote=quote,
        physical_batches=(physical_stock(),),
        legacy_batches=(legacy_stock(),) if mixed else (),
        today=TODAY,
    )
    request = TrainingCheckoutRequest.model_validate(
        {
            "operation_id": uuid4(),
            "sale_id": quote.sale_id,
            "expected_revision_hash": quote.revision_hash,
            "expected_total_amount": quote.total_amount,
            "simulated_payments": [{"method": "cash", "amount": quote.total_amount}],
            "cash_tendered": "2.00",
        }
    )
    headers = {
        "operation_id": request.operation_id,
        "sale_id": quote.sale_id,
        "tenant_id": quote.tenant_id,
        "branch_id": quote.branch_id,
        "register_id": quote.register_id,
        "shift_id": quote.shift_id,
        "cashier_id": quote.cashier_id,
        "receipt_number": "TEST-ACCEPTANCE-0001",
        "receipt_sequence": 1,
        "completed_at": datetime(2026, 10, 26, tzinfo=UTC),
        "total_amount": quote.total_amount,
    }
    receipt = TrainingReceiptDocumentV1.model_validate(
        {
            **headers,
            "lines": training_receipt_lines_from_quote(
                quote, {PHYSICAL: "Training physical", LEGACY: "Training legacy"}
            ),
            "simulated_payments": request.simulated_payments,
            "cash_tendered": "2.00",
            "change_amount": Decimal("2.00") - quote.total_amount,
        }
    )
    return TrainingCheckoutResultV1.model_validate(
        {**headers, "request": request, "receipt": receipt, "simulation": simulation}
    )


@pytest.mark.parametrize("mixed", [False, True])
def test_saved_contract_roundtrip_retains_test_marker_and_historical_precision(mixed: bool) -> None:
    result = saved_result(mixed=mixed)
    assert TrainingCheckoutResultV1.model_validate_json(result.model_dump_json()) == result
    payload = result.model_dump(mode="json")
    assert payload["document_kind"] == "training_checkout"
    assert payload["receipt"]["document_kind"] == "training_sale_receipt"
    assert payload["is_test"] is True and payload["receipt"]["is_test"] is True
    assert payload["receipt"]["lines"][0]["quantity"] == 1
    if mixed:
        assert payload["receipt"]["lines"][1]["quantity"] == "0.125"
    assert not {"stock_command_id", "payment_id", "event_id"} & payload.keys()
    assert all(
        set(part) == {"method", "amount"} for part in payload["receipt"]["simulated_payments"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"is_test": False},
        {"is_test": 1},
        {"receipt_sequence": True},
        {"receipt_number": "REAL-0001"},
        {"document_kind": "physical_checkout"},
        {"total_amount": "0.51"},
        {"sale_id": str(uuid4())},
        {"operation_id": str(uuid4())},
    ],
)
def test_result_rejects_forged_identity_or_real_document_shape(change: dict[str, object]) -> None:
    payload = saved_result().model_dump(mode="json")
    assert persisted_result(payload).model_dump(mode="json") == payload
    with pytest.raises(ValidationError):
        persisted_result({**payload, **change})


@pytest.mark.parametrize(
    "section,field,value",
    [
        pytest.param("receipt", "branch_id", str(uuid4()), id="receipt-branch"),
        ("receipt", "total_amount", "0.51"),
        ("receipt", "is_test", 1),
        ("receipt", "change_amount", "0.00"),
        pytest.param("request", "operation_id", str(uuid4()), id="request-operation"),
        ("request", "expected_revision_hash", "a" * 64),
        ("simulation", "total_amount", "0.51"),
    ],
)
def test_nested_documents_cannot_be_cross_bound(section: str, field: str, value: object) -> None:
    payload = saved_result().model_dump(mode="json")
    assert persisted_result(payload).model_dump(mode="json") == payload
    payload[section][field] = value
    with pytest.raises(ValidationError):
        persisted_result(payload)


@pytest.mark.parametrize("amount", [0.5, True, "0.001", "NaN", "-1.00"])
def test_simulated_money_rejects_floats_booleans_and_invalid_precision(amount: object) -> None:
    payload = saved_result().request.model_dump(mode="json")
    payload["simulated_payments"][0]["amount"] = amount
    with pytest.raises(ValidationError):
        TrainingCheckoutRequest.model_validate(payload)


def test_simulated_allocations_are_unique_balanced_and_have_no_payment_identity() -> None:
    original = saved_result().request.model_dump(mode="json")
    for parts in (
        [{"method": "cash", "amount": "0.25"}, {"method": "cash", "amount": "0.25"}],
        [{"method": "card", "amount": "0.49"}],
        [{"method": "cash", "amount": "0.50", "id": str(uuid4())}],
    ):
        with pytest.raises(ValidationError):
            TrainingCheckoutRequest.model_validate({**original, "simulated_payments": parts})
    with pytest.raises(ValidationError):
        TrainingCheckoutRequest.model_validate(
            {
                **original,
                "simulated_payments": [{"method": "card", "amount": "0.50"}],
                "cash_tendered": "0.01",
            }
        )


def test_training_receipt_cannot_change_frozen_format_or_amount() -> None:
    original = saved_result(mixed=True).model_dump(mode="json")
    assert persisted_result(original).model_dump(mode="json") == original
    for field, value in (("quantity", 2), ("unit_label", "уп."), ("net_amount", "0.51")):
        payload = deepcopy(original)
        payload["receipt"]["lines"][0][field] = value
        with pytest.raises(ValidationError):
            persisted_result(payload)


def test_recovery_envelope_is_bound_to_one_saved_operation() -> None:
    result = saved_result()
    with pytest.raises(ValidationError):
        TrainingCheckoutOperationCompleted(
            operation_id=uuid4(), sale_id=result.sale_id, result=result
        )


def test_request_hash_and_saved_validation_are_independent_of_decimal_context() -> None:
    result = saved_result(mixed=True)
    payload = result.model_dump(mode="json")
    expected = training_checkout_request_hash(result.request)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert persisted_result(payload) == result
        assert training_checkout_request_hash(result.request) == expected
