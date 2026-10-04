"""Exact allocation contracts; no database or operational refund flow is involved."""

from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, InvalidOperation, Rounded, localcontext
from typing import Literal, cast

import pytest

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, MAX_MONEY
from app.domains.pos.unit_money import allocate_unit_amount


def test_ten_tjs_pack_distributes_cents_without_loss() -> None:
    amounts = [
        allocate_unit_amount(Decimal("10.00"), total_units=30, offset=index, quantity=1).amount
        for index in range(30)
    ]
    assert amounts[:3] == [Decimal("0.33"), Decimal("0.33"), Decimal("0.34")]
    assert amounts == amounts[:3] * 10
    assert sum(amounts, Decimal("0.00")) == Decimal("10.00")


@pytest.mark.parametrize("total", [Decimal("0.00"), Decimal("0.01")])
def test_zero_and_one_cent_pack_conserve_amount(total: Decimal) -> None:
    amounts = [
        allocate_unit_amount(total, total_units=30, offset=index, quantity=1).amount
        for index in range(30)
    ]
    assert amounts[:-1] == [Decimal("0.00")] * 29
    assert amounts[-1] == total
    assert sum(amounts, Decimal("0.00")) == total


def test_adjacent_ranges_are_independent_of_grouping() -> None:
    # Exhaust all valid slices and all their split points for small pack sizes.
    for total_units in range(1, 9):
        for cents in range(21):
            total = Decimal(cents) / 100
            units = [
                allocate_unit_amount(
                    total, total_units=total_units, offset=index, quantity=1
                ).amount
                for index in range(total_units)
            ]
            assert sum(units, Decimal("0.00")) == total
            assert min(units) >= Decimal("0.00")
            assert max(units) - min(units) <= Decimal("0.01")
            for offset in range(total_units):
                for quantity in range(1, total_units - offset + 1):
                    whole = allocate_unit_amount(
                        total,
                        total_units=total_units,
                        offset=offset,
                        quantity=quantity,
                    ).amount
                    assert whole == sum(units[offset : offset + quantity])
                    for first_quantity in range(1, quantity):
                        first = allocate_unit_amount(
                            total,
                            total_units=total_units,
                            offset=offset,
                            quantity=first_quantity,
                        ).amount
                        second = allocate_unit_amount(
                            total,
                            total_units=total_units,
                            offset=offset + first_quantity,
                            quantity=quantity - first_quantity,
                        ).amount
                        assert first + second == whole


def test_maximum_money_and_quantity_conserve_amount() -> None:
    split = MAX_BASE_UNITS // 2
    first = allocate_unit_amount(MAX_MONEY, total_units=MAX_BASE_UNITS, offset=0, quantity=split)
    second = allocate_unit_amount(
        MAX_MONEY,
        total_units=MAX_BASE_UNITS,
        offset=split,
        quantity=MAX_BASE_UNITS - split,
    )
    assert first.amount == Decimal("499999999994.99")
    assert second.amount == Decimal("500000000005.00")
    assert first.amount + second.amount == MAX_MONEY


def test_last_partial_refund_uses_original_discounted_line_amount() -> None:
    original_net = Decimal("9.99")
    refunded = [
        allocate_unit_amount(original_net, total_units=30, offset=0, quantity=7),
        allocate_unit_amount(original_net, total_units=30, offset=7, quantity=22),
        allocate_unit_amount(original_net, total_units=30, offset=29, quantity=1),
    ]
    assert [part.amount for part in refunded] == [
        Decimal("2.33"),
        Decimal("7.32"),
        Decimal("0.34"),
    ]
    assert sum((part.amount for part in refunded), Decimal("0.00")) == original_net


@pytest.mark.parametrize("total", [Decimal("1.2300"), Decimal("0E+100000")])
def test_result_is_frozen_and_money_is_canonical(total: Decimal) -> None:
    result = allocate_unit_amount(total, total_units=1, offset=0, quantity=1)
    assert result.version == 1
    assert result.currency == "TJS"
    assert (result.total_units, result.offset, result.quantity) == (1, 0, 1)
    assert result.total_amount == total
    assert result.amount == total
    assert result.total_amount.as_tuple().exponent == -2
    assert result.amount.as_tuple().exponent == -2
    with pytest.raises(FrozenInstanceError):
        result.amount = Decimal("2.00")


def test_allocation_ignores_ambient_decimal_precision_and_traps() -> None:
    total = Decimal("999999999999.9900")
    expected_total = Decimal("999999999999.99")
    expected_amount = Decimal("333333333333.33")
    tiny = Decimal("1E-100000")
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.traps[InvalidOperation] = True
        result = allocate_unit_amount(total, total_units=3, offset=2, quantity=1)
        assert result.total_amount == expected_total
        assert result.amount == expected_amount
        assert result.total_amount.as_tuple().exponent == -2
        assert result.amount.as_tuple().exponent == -2
        with pytest.raises(ValueError):
            allocate_unit_amount(tiny, total_units=3, offset=0, quantity=1)
        assert not any(context.flags.values())


@pytest.mark.parametrize(
    "total",
    [
        None,
        True,
        1,
        1.0,
        "1.00",
        Decimal("-0.01"),
        Decimal("1.001"),
        Decimal("1E-100000"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("1000000000000.00"),
    ],
)
def test_invalid_money_is_rejected_without_coercion(total: object) -> None:
    with pytest.raises(ValueError):
        allocate_unit_amount(cast(Decimal, total), total_units=3, offset=0, quantity=1)


@pytest.mark.parametrize("value", [None, True, False, 1.0, "1", Decimal("1")])
@pytest.mark.parametrize("field", ["total_units", "offset", "quantity", "version"])
def test_integer_fields_reject_coercible_types(field: str, value: object) -> None:
    total_units = cast(int, value) if field == "total_units" else 3
    offset = cast(int, value) if field == "offset" else 0
    quantity = cast(int, value) if field == "quantity" else 1
    version = cast(int, value) if field == "version" else 1
    with pytest.raises(ValueError):
        allocate_unit_amount(
            Decimal("1.00"),
            total_units=total_units,
            offset=offset,
            quantity=quantity,
            version=version,
        )


@pytest.mark.parametrize(
    "total_units, offset, quantity",
    [
        (0, 0, 1),
        (-1, 0, 1),
        (MAX_BASE_UNITS + 1, 0, 1),
        (3, -1, 1),
        (3, 3, 1),
        (3, 0, 0),
        (3, 0, -1),
        (3, 0, 4),
        (3, 2, 2),
    ],
)
def test_invalid_ranges_are_rejected(total_units: int, offset: int, quantity: int) -> None:
    with pytest.raises(ValueError):
        allocate_unit_amount(
            Decimal("1.00"),
            total_units=total_units,
            offset=offset,
            quantity=quantity,
        )


@pytest.mark.parametrize("version", [-1, 0, 2])
def test_unknown_versions_are_rejected(version: int) -> None:
    with pytest.raises(ValueError):
        allocate_unit_amount(Decimal("1.00"), total_units=1, offset=0, quantity=1, version=version)


@pytest.mark.parametrize("currency", [None, 1, True, "", "tjs", "USD"])
def test_unsupported_currency_is_rejected(currency: object) -> None:
    with pytest.raises(ValueError):
        allocate_unit_amount(
            Decimal("1.00"),
            total_units=1,
            offset=0,
            quantity=1,
            currency=cast(Literal["TJS"], currency),
        )
