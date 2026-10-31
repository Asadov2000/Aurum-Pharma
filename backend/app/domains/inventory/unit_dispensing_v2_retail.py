"""Internal retail snapshots for physical carts; not a sale, payment or receipt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, localcontext
from typing import Literal, Protocol, cast
from uuid import UUID

from pydantic import Field, field_validator

from app.core.errors import BusinessRuleError
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.unit_sales import MAX_MONEY, UnitSalePreparation
from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2
from app.domains.inventory.unit_dispensing_v2_cost import UnitCartDispenseCostResultV2
from app.domains.inventory.unit_ledger_v2_models import UnitStockCartLineV2
from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2, to_base_units_v2
from app.domains.pos.unit_money import allocate_unit_amount


class UnitRetailCatalogV2(Protocol):
    @property
    def is_active(self) -> bool: ...

    @property
    def deleted_at(self) -> datetime | None: ...

    @property
    def currency(self) -> str: ...

    @property
    def unit_sale_preparation(self) -> object: ...

    @property
    def base_price(self) -> Decimal | None: ...


class UnitPricedCartLineV2(UnitCartLineV2):
    discount_amount: Decimal = Field(
        default=Decimal("0.00"), ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False
    )

    @field_validator("discount_amount")
    @classmethod
    def canonical_zero_discount(cls, value: Decimal) -> Decimal:
        # PostgreSQL NUMERIC has no signed zero; replay hashes must use the same bytes.
        return Decimal("0.00") if value.is_zero() else value


@dataclass(frozen=True)
class UnitRetailAmountsV2:
    original_retail_unit_price: Decimal
    gross_amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal


@dataclass(frozen=True)
class UnitCartRetailLineV2:
    id: UUID
    catalog_id: UUID
    packaging: UnitPackagingV2
    sale_format: SaleFormatV2
    quantity: int
    base_units: int
    original_retail_unit_price: Decimal
    gross_amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal


@dataclass(frozen=True)
class UnitCartRetailResultV2:
    command_id: UUID
    lines: tuple[UnitCartRetailLineV2, ...]
    cost_result: UnitCartDispenseCostResultV2
    total_amount: Decimal
    currency: Literal["TJS"] = "TJS"
    money_version: Literal[1] = 1


def quote_retail_line_v2(
    line: UnitCartLineV2, catalog: TenantCatalog | UnitRetailCatalogV2, discount: Decimal
) -> UnitRetailAmountsV2:
    """Use explicit format prices and exact cents, never divide a pack price."""
    if (
        not catalog.is_active
        or catalog.deleted_at is not None
        or catalog.currency != "TJS"
        or catalog.unit_sale_preparation is None
    ):
        raise BusinessRuleError("Physical cart item has no available retail configuration")
    try:
        preparation = UnitSalePreparation.model_validate(catalog.unit_sale_preparation)
        if (
            preparation.unit,
            preparation.units_per_pack,
            preparation.units_per_blister,
            preparation.dispense_step,
        ) != (
            line.packaging.unit,
            line.packaging.units_per_pack,
            line.packaging.units_per_blister,
            line.packaging.dispense_step,
        ):
            raise ValueError("Catalog composition differs from physical cart")
        to_base_units_v2(line.packaging, quantity=line.quantity, sale_format=line.sale_format)
        discount = allocate_unit_amount(discount, total_units=1, offset=0, quantity=1).total_amount
        with localcontext(Context(prec=40)):
            price = preparation.unit_price
            if line.sale_format == "pack":
                if catalog.base_price is None:
                    raise ValueError("Pack retail price is required")
                price = catalog.base_price
            elif line.sale_format == "blister":
                if line.packaging.units_per_blister is None:
                    raise ValueError("Blister composition is unknown")
                price *= line.packaging.units_per_blister
            price = allocate_unit_amount(price, total_units=1, offset=0, quantity=1).total_amount
            gross = price * line.quantity
            if not 0 < price <= MAX_MONEY or not 0 < gross <= MAX_MONEY:
                raise ValueError("Retail amount is outside the money limit")
            if discount >= gross:
                raise ValueError("Discount must leave a positive amount")
            return UnitRetailAmountsV2(price, gross, discount, gross - discount)
    except ValueError as exc:
        raise BusinessRuleError("Invalid physical cart retail price or discount") from exc


def persisted_retail_result_v2(
    command_id: UUID,
    lines: list[UnitStockCartLineV2],
    cost_result: UnitCartDispenseCostResultV2,
) -> UnitCartRetailResultV2:
    snapshots: list[UnitCartRetailLineV2] = []
    with localcontext(Context(prec=40)):
        for line in lines:
            price, gross, discount, net = (
                line.original_retail_unit_price,
                line.gross_amount,
                line.discount_amount,
                line.net_amount,
            )
            if price is None or gross is None or discount is None or net is None:
                raise BusinessRuleError("Historical physical cart retail amounts are unknown")
            if line.sale_format not in ("pack", "blister", "unit"):
                raise BusinessRuleError("Invalid saved retail format")
            for amount in (price, gross, discount, net):
                if (
                    not amount.is_finite()
                    or not 0 <= amount <= MAX_MONEY
                    or amount != amount.quantize(Decimal("0.01"))
                ):
                    raise BusinessRuleError("Invalid saved retail amount")
            if price <= 0 or gross != price * line.quantity or net != gross - discount or net <= 0:
                raise BusinessRuleError("Invalid saved retail arithmetic")
            snapshots.append(
                UnitCartRetailLineV2(
                    line.line_id,
                    line.catalog_id,
                    UnitPackagingV2.model_validate(line.packaging),
                    cast(SaleFormatV2, line.sale_format),
                    line.quantity,
                    line.base_units,
                    price,
                    gross,
                    discount,
                    net,
                )
            )
        total = sum((line.net_amount for line in snapshots), Decimal("0.00"))
        if not snapshots or total > MAX_MONEY:
            raise BusinessRuleError("Invalid saved retail cart total")
    return UnitCartRetailResultV2(command_id, tuple(snapshots), cost_result, total)
