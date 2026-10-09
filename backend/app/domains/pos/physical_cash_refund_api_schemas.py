"""Public refund selections and previews; amounts and stock history are server-owned."""

from typing import Literal, Self
from uuid import UUID

from pydantic import UUID4, Field, model_validator

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_contracts import FrozenContract, Money, WholeQuantity

RefundReasonMode = Literal["off", "optional", "required", "required_with_text"]


class PhysicalRefundSelection(FrozenContract):
    line_id: UUID
    sale_format: SaleFormatV2
    quantity: WholeQuantity


class PhysicalRefundQuoteRequest(FrozenContract):
    items: tuple[PhysicalRefundSelection, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_items(self) -> Self:
        if len({item.line_id for item in self.items}) != len(self.items):
            raise ValueError("Refund lines must be unique")
        return self

    def selections(self) -> tuple[PhysicalReturnSelection, ...]:
        return tuple(
            PhysicalReturnSelection(item.line_id, item.sale_format, item.quantity)
            for item in self.items
        )


class PhysicalRefundCompleteRequest(PhysicalRefundQuoteRequest):
    operation_id: UUID4
    shift_id: UUID
    expected_total_amount: Money
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    reason_code: RefundReasonCode | None = None
    comment: str | None = Field(default=None, max_length=500, strict=True)


class PhysicalRefundOptionLine(FrozenContract):
    line_id: UUID
    name: str
    unit_label: str
    packaging: UnitPackagingV2
    sold_base_units: WholeQuantity
    returned_base_units: int = Field(strict=True, ge=0)
    remaining_base_units: int = Field(strict=True, ge=0)
    original_sale_format: SaleFormatV2
    original_retail_unit_price: Money


class PhysicalRefundOptions(FrozenContract):
    parent_sale_id: UUID
    branch_id: UUID
    register_id: UUID
    original_receipt_number: str
    reason_mode: RefundReasonMode
    lines: tuple[PhysicalRefundOptionLine, ...]


class PhysicalRefundQuoteLine(FrozenContract):
    line_id: UUID
    sale_format: SaleFormatV2
    quantity: WholeQuantity
    base_units: WholeQuantity
    offset: int = Field(strict=True, ge=0)
    gross_amount: Money
    discount_amount: Money
    net_amount: Money


class PhysicalRefundQuote(FrozenContract):
    parent_sale_id: UUID
    revision_hash: str
    total_amount: Money
    currency: Literal["TJS"] = "TJS"
    lines: tuple[PhysicalRefundQuoteLine, ...]
