"""Public contracts for unpaid physical cart preparation; no checkout activation."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.inventory.unit_stock_v2 import SaleFormatV2
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
