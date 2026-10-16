"""Payment allocations are exact, explicit and cannot pretend to confirm a payment."""

from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domains.pos.unit_payment_plan import UnitPaymentPlanRequest


def payload() -> dict[str, object]:
    return {
        "sale_id": str(uuid4()),
        "expected_revision_hash": "a" * 64,
        "expected_total_amount": "10.00",
        "payments": [{"method": "card", "amount": "10.00"}],
    }


@pytest.mark.parametrize("amount", ["9.99", "10.01", "0.00", "-1.00", "NaN", "10.001"])
def test_plan_rejects_invalid_or_unbalanced_amount(amount: str) -> None:
    with pytest.raises(ValidationError):
        UnitPaymentPlanRequest.model_validate(
            {**payload(), "payments": [{"method": "card", "amount": amount}]}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"payments": []},
        {"payments": [{"method": "bank_transfer", "amount": "10.00"}]},
        {
            "payments": [{"method": "cash", "amount": "10.00"}],
            "cash_tendered": "10.00",
        },
        {
            "payments": [
                {"method": "card", "amount": "5.00"},
                {"method": "card", "amount": "5.00"},
            ]
        },
        {"cash_tendered": "10.00"},
        {"currency": "USD"},
        {"confirmed": True},
        {"operation_id": str(uuid4())},
    ],
)
def test_plan_rejects_ambiguous_or_financial_input(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        UnitPaymentPlanRequest.model_validate({**payload(), **change})


@pytest.mark.parametrize("tendered", [None, "4.99"])
def test_cash_part_requires_sufficient_tendered_amount(tendered: str | None) -> None:
    with pytest.raises(ValidationError):
        UnitPaymentPlanRequest.model_validate(
            {
                **payload(),
                "payments": [
                    {"method": "cash", "amount": "5.00"},
                    {"method": "card", "amount": "5.00"},
                ],
                "cash_tendered": tendered,
            }
        )


def test_exact_split_ignores_ambient_decimal_precision() -> None:
    with localcontext() as context:
        context.prec = 2
        request = UnitPaymentPlanRequest.model_validate(
            {
                **payload(),
                "payments": [
                    {"method": "cash", "amount": "0.01"},
                    {"method": "card", "amount": "9.99"},
                ],
                "cash_tendered": "5.00",
            }
        )
    assert request.expected_total_amount == Decimal("10.00")
    assert request.payments[0].amount == Decimal("0.01")
