"""Preparation contracts and exact arithmetic; these do not enable unit sales."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

SaleFormat = Literal["pack", "unit"]
DispenseUnit = Literal["tablet", "capsule", "ampoule", "sachet", "suppository"]

# Future quantities must also fit the existing NUMERIC(14,3) storage boundary.
MAX_BASE_UNITS = 99_999_999_999
MAX_MONEY = Decimal("999999999999.99")
_Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
_money_adapter: TypeAdapter[Decimal] = TypeAdapter(_Money)


class UnitSalePreparation(BaseModel):
    """Explicit packaging information, saved for review before operational rollout."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    state: Literal["prepared"] = "prepared"
    unit: DispenseUnit
    units_per_pack: int = Field(strict=True, ge=2, le=10_000)
    units_per_blister: int | None = Field(default=None, strict=True, ge=2, le=10_000)
    dispense_step: int = Field(strict=True, ge=1, le=10_000)
    unit_price: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_step(self) -> Self:
        if self.units_per_pack % self.dispense_step:
            raise ValueError("dispense_step must divide units_per_pack")
        if self.units_per_blister is not None and self.units_per_pack % self.units_per_blister:
            raise ValueError("units_per_blister must divide units_per_pack")
        return self

    @model_serializer(mode="wrap")
    def serialize_preparation(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        # Keep legacy metadata and immutable operation hashes byte-for-byte compatible.
        result: dict[str, object] = handler(self)
        if self.units_per_blister is None:
            result.pop("units_per_blister", None)
        return result

    @field_serializer("unit_price", when_used="json")
    def serialize_unit_price(self, value: Decimal) -> str:
        return format(value, ".2f")


@dataclass(frozen=True)
class UnitSaleQuote:
    """A calculation result, never authorization or a persisted sale snapshot."""

    sale_format: SaleFormat
    quantity: int
    base_units: int
    unit_price: Decimal
    total_price: Decimal


def _whole_quantity(quantity: Decimal) -> int:
    if (
        not isinstance(quantity, Decimal)
        or not quantity.is_finite()
        or quantity <= 0
        or quantity > MAX_BASE_UNITS
        or quantity != quantity.to_integral_value()
    ):
        raise ValueError("quantity must be a positive whole number within the stock limit")
    return int(quantity)


def to_base_units(
    preparation: UnitSalePreparation, *, quantity: Decimal, sale_format: SaleFormat
) -> int:
    """Convert an explicit format to whole units without rounding or parsing pack text."""
    count = _whole_quantity(quantity)
    if sale_format == "pack":
        base_units = count * preparation.units_per_pack
    elif sale_format == "unit":
        if count % preparation.dispense_step:
            raise ValueError("unit quantity must be a multiple of dispense_step")
        base_units = count
    else:
        raise ValueError("unknown sale format")
    if base_units > MAX_BASE_UNITS:
        raise ValueError("base quantity exceeds the stock limit")
    return base_units


def quote_unit_sale(
    preparation: UnitSalePreparation,
    *,
    quantity: Decimal,
    sale_format: SaleFormat,
    pack_price: Decimal | None = None,
) -> UnitSaleQuote:
    """Quote explicit retail prices; never infer a unit price by dividing a pack price."""
    base_units = to_base_units(preparation, quantity=quantity, sale_format=sale_format)
    price = preparation.unit_price
    if sale_format == "pack":
        if pack_price is None or not isinstance(pack_price, Decimal):
            raise ValueError("pack price must be explicitly provided as Decimal")
        price = _money_adapter.validate_python(pack_price)
    count = int(quantity)
    with localcontext() as context:
        context.prec = 40
        total = price * count
        if total > MAX_MONEY:
            raise ValueError("sale total exceeds the money limit")
        return UnitSaleQuote(
            sale_format=sale_format,
            quantity=count,
            base_units=base_units,
            unit_price=price.quantize(Decimal("0.01")),
            total_price=total.quantize(Decimal("0.01")),
        )
