"""Public mixed refund selections and retail previews without procurement facts."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import UUID4, Field, model_validator

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection, MixedReturnSelection
from app.domains.pos.mixed_return_journal_schemas import LegacyOffset
from app.domains.pos.physical_cash_refund_api_schemas import RefundReasonMode
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_contracts import FrozenContract, LegacyQuantity, Money, WholeQuantity


class MixedLegacyRefundSelection(FrozenContract):
    inventory_kind: Literal["legacy_pack"]
    line_id: UUID
    quantity: LegacyQuantity


class MixedPhysicalRefundSelection(FrozenContract):
    inventory_kind: Literal["physical_unit"]
    line_id: UUID
    sale_format: SaleFormatV2
    quantity: WholeQuantity


MixedRefundSelection = Annotated[
    MixedLegacyRefundSelection | MixedPhysicalRefundSelection,
    Field(discriminator="inventory_kind"),
]


class MixedRefundQuoteRequest(FrozenContract):
    items: tuple[MixedRefundSelection, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_items(self) -> Self:
        if len({item.line_id for item in self.items}) != len(self.items):
            raise ValueError("Refund lines must be unique across inventory kinds")
        return self

    def selections(self) -> tuple[MixedReturnSelection, ...]:
        return tuple(
            (
                LegacyReturnSelection(item.line_id, item.quantity)
                if isinstance(item, MixedLegacyRefundSelection)
                else PhysicalReturnSelection(item.line_id, item.sale_format, item.quantity)
            )
            for item in self.items
        )


class MixedRefundCompleteRequest(MixedRefundQuoteRequest):
    operation_id: UUID4
    shift_id: UUID
    expected_total_amount: Money
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    reason_code: RefundReasonCode | None = None
    comment: str | None = Field(default=None, max_length=500, strict=True)


class MixedLegacyRefundOptionLine(FrozenContract):
    inventory_kind: Literal["legacy_pack"] = "legacy_pack"
    line_id: UUID
    name: str
    unit_label: Literal["уп."] = "уп."
    sold_quantity: LegacyQuantity
    returned_quantity: LegacyOffset
    remaining_quantity: LegacyOffset
    original_retail_unit_price: Money


class MixedPhysicalRefundOptionLine(FrozenContract):
    inventory_kind: Literal["physical_unit"] = "physical_unit"
    line_id: UUID
    name: str
    unit_label: str
    packaging: UnitPackagingV2
    sold_base_units: WholeQuantity
    returned_base_units: int = Field(strict=True, ge=0)
    remaining_base_units: int = Field(strict=True, ge=0)
    original_sale_format: SaleFormatV2
    original_retail_unit_price: Money


MixedRefundOptionLine = Annotated[
    MixedLegacyRefundOptionLine | MixedPhysicalRefundOptionLine,
    Field(discriminator="inventory_kind"),
]


class MixedRefundOptions(FrozenContract):
    parent_sale_id: UUID
    branch_id: UUID
    register_id: UUID
    original_receipt_number: str
    reason_mode: RefundReasonMode
    lines: tuple[MixedRefundOptionLine, ...]


class MixedLegacyRefundQuoteLine(FrozenContract):
    inventory_kind: Literal["legacy_pack"] = "legacy_pack"
    line_id: UUID
    quantity: LegacyQuantity
    offset: LegacyOffset
    gross_amount: Money
    discount_amount: Money
    net_amount: Money


class MixedPhysicalRefundQuoteLine(FrozenContract):
    inventory_kind: Literal["physical_unit"] = "physical_unit"
    line_id: UUID
    sale_format: SaleFormatV2
    quantity: WholeQuantity
    base_units: WholeQuantity
    offset: int = Field(strict=True, ge=0)
    gross_amount: Money
    discount_amount: Money
    net_amount: Money


MixedRefundQuoteLine = Annotated[
    MixedLegacyRefundQuoteLine | MixedPhysicalRefundQuoteLine,
    Field(discriminator="inventory_kind"),
]


class MixedRefundQuote(FrozenContract):
    parent_sale_id: UUID
    revision_hash: str
    total_amount: Money
    currency: Literal["TJS"] = "TJS"
    lines: tuple[MixedRefundQuoteLine, ...]
