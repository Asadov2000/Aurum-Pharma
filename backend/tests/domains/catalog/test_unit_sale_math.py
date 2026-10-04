"""Preparation validation and exact arithmetic without inventory or sale effects."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
from typing import cast

import pytest
from pydantic import ValidationError

from app.domains.catalog.unit_sales import (
    SaleFormat,
    UnitSalePreparation,
    quote_unit_sale,
    to_base_units,
)


def _preparation(**overrides: object) -> UnitSalePreparation:
    payload: dict[str, object] = {
        "unit": "tablet",
        "units_per_pack": 30,
        "dispense_step": 1,
        "unit_price": Decimal("3.33"),
    }
    payload.update(overrides)
    return UnitSalePreparation.model_validate(payload)


@pytest.mark.parametrize("unit", ["tablet", "capsule", "ampoule", "sachet", "suppository"])
def test_preparation_requires_an_explicit_supported_unit(unit: str) -> None:
    preparation = _preparation(unit=unit)

    assert preparation.unit == unit
    assert preparation.version == 1
    assert preparation.state == "prepared"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("version", False),
        ("version", 1.0),
        ("version", "1"),
        ("version", 2),
        ("version", None),
        ("state", "enabled"),
        ("unit", "pack"),
        ("unit", "blister"),
        ("unit", None),
        ("units_per_pack", 1),
        ("units_per_pack", 10_001),
        ("units_per_pack", "30"),
        ("units_per_pack", 30.0),
        ("units_per_pack", True),
        ("dispense_step", 0),
        ("dispense_step", 10_001),
        ("dispense_step", "1"),
        ("dispense_step", 1.0),
        ("dispense_step", True),
        ("dispense_step", 7),
        ("dispense_step", 60),
        ("unit_price", Decimal("0")),
        ("unit_price", Decimal("-0.01")),
        ("unit_price", Decimal("0.001")),
        ("unit_price", Decimal("1000000000000.00")),
        ("unit_price", Decimal("NaN")),
        ("unit_price", Decimal("sNaN")),
        ("unit_price", Decimal("Infinity")),
        ("unit_price", Decimal("-Infinity")),
        ("enabled", True),
    ],
)
def test_preparation_rejects_malformed_or_operational_configuration(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _preparation(**{field: value})


@pytest.mark.parametrize("missing", ["unit", "units_per_pack", "dispense_step", "unit_price"])
def test_preparation_never_infers_missing_packaging_information(missing: str) -> None:
    payload = _preparation().model_dump()
    del payload[missing]

    with pytest.raises(ValidationError):
        UnitSalePreparation.model_validate(payload)


@pytest.mark.parametrize(
    ("units_per_pack", "dispense_step"), [(2, 1), (30, 5), (30, 30), (10_000, 10_000)]
)
def test_preparation_accepts_packaging_boundaries_and_whole_dispense_steps(
    units_per_pack: int, dispense_step: int
) -> None:
    preparation = _preparation(units_per_pack=units_per_pack, dispense_step=dispense_step)

    assert preparation.units_per_pack == units_per_pack
    assert preparation.dispense_step == dispense_step


@pytest.mark.parametrize(
    ("price", "serialized"),
    [("0.01", "0.01"), ("1", "1.00"), ("1.5", "1.50"), ("999999999999.99", "999999999999.99")],
)
def test_preparation_json_preserves_money_and_round_trips(price: str, serialized: str) -> None:
    preparation = _preparation(unit_price=Decimal(price))

    assert preparation.model_dump(mode="json")["unit_price"] == serialized
    assert UnitSalePreparation.model_validate_json(preparation.model_dump_json()) == preparation


def test_equivalent_price_scales_have_one_json_representation() -> None:
    assert _preparation(unit_price=Decimal("3.3")).model_dump(mode="json") == _preparation(
        unit_price=Decimal("3.30")
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    ("units_per_pack", "step", "quantity", "sale_format", "expected"),
    [
        (30, 1, "1", "pack", 30),
        (30, 1, "2.000", "pack", 60),
        (30, 1, "1", "unit", 1),
        (30, 5, "10", "unit", 10),
        (30, 30, "30", "unit", 30),
        (10_000, 10_000, "1", "pack", 10_000),
        (30, 1, "3333333333", "pack", 99_999_999_990),
        (2, 1, "99999999999", "unit", 99_999_999_999),
    ],
)
def test_conversion_uses_exact_whole_base_units(
    units_per_pack: int, step: int, quantity: str, sale_format: SaleFormat, expected: int
) -> None:
    preparation = _preparation(units_per_pack=units_per_pack, dispense_step=step)

    converted = to_base_units(preparation, quantity=Decimal(quantity), sale_format=sale_format)

    assert type(converted) is int
    assert converted == expected


@pytest.mark.parametrize(
    "quantity",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("0.5"),
        Decimal("1.001"),
        Decimal("1E-1000"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("100000000000"),
        Decimal("1E+1000"),
        "1",
        1,
        1.0,
        True,
        None,
    ],
)
@pytest.mark.parametrize("sale_format", ["pack", "unit"])
def test_conversion_rejects_quantities_instead_of_rounding_or_coercing(
    quantity: object, sale_format: SaleFormat
) -> None:
    with pytest.raises(ValueError):
        to_base_units(_preparation(), quantity=cast(Decimal, quantity), sale_format=sale_format)


@pytest.mark.parametrize(
    ("quantity", "sale_format"), [("1", "unit"), ("7", "unit"), ("3333333334", "pack")]
)
def test_conversion_rejects_incomplete_dispense_steps_and_converted_overflow(
    quantity: str, sale_format: SaleFormat
) -> None:
    with pytest.raises(ValueError):
        to_base_units(
            _preparation(dispense_step=5), quantity=Decimal(quantity), sale_format=sale_format
        )


@pytest.mark.parametrize("sale_format", ["blister", "", None])
def test_conversion_rejects_unknown_sale_formats(sale_format: object) -> None:
    with pytest.raises(ValueError):
        to_base_units(
            _preparation(), quantity=Decimal("1"), sale_format=cast(SaleFormat, sale_format)
        )


def test_thirty_individual_units_conserve_one_pack_without_fractional_pack_rounding() -> None:
    preparation = _preparation(units_per_pack=30)

    individual_units = sum(
        to_base_units(preparation, quantity=Decimal("1"), sale_format="unit") for _ in range(30)
    )
    whole_pack = to_base_units(preparation, quantity=Decimal("1"), sale_format="pack")

    assert individual_units == whole_pack == 30


@pytest.mark.parametrize(
    ("sale_format", "quantity", "pack_price", "expected_price", "expected_total", "expected_base"),
    [
        ("pack", "1", "1.00", "1.00", "1.00", 3),
        ("unit", "3", "1.00", "0.34", "1.02", 3),
        ("pack", "2", "9.99", "9.99", "19.98", 6),
        ("unit", "2", None, "0.34", "0.68", 2),
        ("unit", "2", "999.00", "0.34", "0.68", 2),
    ],
)
def test_quote_uses_the_explicit_price_for_each_sale_format(
    sale_format: SaleFormat,
    quantity: str,
    pack_price: str | None,
    expected_price: str,
    expected_total: str,
    expected_base: int,
) -> None:
    quote = quote_unit_sale(
        _preparation(units_per_pack=3, unit_price=Decimal("0.34")),
        quantity=Decimal(quantity),
        sale_format=sale_format,
        pack_price=Decimal(pack_price) if pack_price is not None else None,
    )

    assert quote.sale_format == sale_format
    assert quote.quantity == int(quantity)
    assert quote.base_units == expected_base
    assert quote.unit_price == Decimal(expected_price)
    assert quote.total_price == Decimal(expected_total)


@pytest.mark.parametrize(
    "pack_price",
    [
        None,
        "1.00",
        1,
        1.0,
        True,
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("1.001"),
        Decimal("1000000000000.00"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
    ],
)
def test_pack_quote_requires_explicit_valid_decimal_money(pack_price: object) -> None:
    with pytest.raises(ValueError):
        quote_unit_sale(
            _preparation(),
            quantity=Decimal("1"),
            sale_format="pack",
            pack_price=cast(Decimal | None, pack_price),
        )


@pytest.mark.parametrize("sale_format", ["pack", "unit"])
def test_quote_rejects_total_overflow_for_either_format(sale_format: SaleFormat) -> None:
    with pytest.raises(ValueError, match="money limit"):
        quote_unit_sale(
            _preparation(unit_price=Decimal("999999999999.99")),
            quantity=Decimal("2"),
            sale_format=sale_format,
            pack_price=Decimal("999999999999.99"),
        )


def test_quote_is_exact_at_money_boundary_and_under_low_ambient_precision() -> None:
    preparation = _preparation(unit_price=Decimal("0.01"))

    with localcontext() as context:
        context.prec = 2
        pack_quote = quote_unit_sale(
            preparation,
            quantity=Decimal("1"),
            sale_format="pack",
            pack_price=Decimal("999999999999.99"),
        )
        unit_quote = quote_unit_sale(
            preparation, quantity=Decimal("99999999999"), sale_format="unit"
        )

    assert pack_quote.total_price == Decimal("999999999999.99")
    assert unit_quote.total_price == Decimal("999999999.99")
    assert unit_quote.base_units == 99_999_999_999


def test_preparation_and_quote_cannot_be_modified_after_validation() -> None:
    preparation = _preparation()
    quote = quote_unit_sale(preparation, quantity=Decimal("1"), sale_format="unit")

    with pytest.raises(ValidationError, match="frozen"):
        preparation.unit_price = Decimal("9.99")
    with pytest.raises(FrozenInstanceError):
        quote.total_price = Decimal("9.99")
