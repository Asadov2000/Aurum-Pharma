"""Public contracts for unpaid physical cart preparation; no checkout activation."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2
from app.domains.pos.unit_contracts import FrozenContract, Money, Position, WholeQuantity


class PhysicalCartItemPut(FrozenContract):
    catalog_id: UUID
    position: Annotated[Position, Field(le=200)]
    sale_format: SaleFormatV2
    quantity: WholeQuantity
    discount_amount: Money = Decimal("0.00")


class PhysicalCartQuoteRead(FrozenContract):
    quote: MixedCartQuoteV2
    revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


PhysicalOptionUnavailableReason = Literal[
    "not_prepared", "catalog_unavailable", "unknown_blister", "price_unavailable"
]


class PhysicalCartFormatOption(FrozenContract):
    """Configuration facts only; configured never authorizes a sale or allocation."""

    sale_format: SaleFormatV2
    configured: bool
    quantity_step: Annotated[int, Field(strict=True, ge=1)] | None
    retail_unit_price: Money | None
    unavailable_reason: PhysicalOptionUnavailableReason | None


class PhysicalCartStockRead(FrozenContract):
    """Shared raw quantities; the three counts are not independent selling limits."""

    base_units: Annotated[int, Field(strict=True, ge=0)]
    closed_packs: Annotated[int, Field(strict=True, ge=0)]
    intact_blisters: Annotated[int, Field(strict=True, ge=0)] | None


class PhysicalCartOptionsRead(FrozenContract):
    state: Literal["physical_options_preview"] = "physical_options_preview"
    tenant_id: UUID
    branch_id: UUID
    sale_id: UUID
    catalog_id: UUID
    stock_date: date
    packaging: UnitPackagingV2 | None
    currency: Literal["TJS"] = "TJS"
    checkout_enabled: Literal[True] = True
    stock_reserved: Literal[False] = False
    stock: PhysicalCartStockRead
    options: tuple[PhysicalCartFormatOption, PhysicalCartFormatOption, PhysicalCartFormatOption]
