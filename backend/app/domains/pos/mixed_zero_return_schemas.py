"""Cost-free acknowledgments of mixed quarantine intake, never cash receipts."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import UUID4, Field, model_validator

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_cash_refund_api_schemas import MixedRefundQuoteRequest
from app.domains.pos.mixed_return_journal_schemas import LegacyOffset
from app.domains.pos.unit_contracts import FrozenContract, LegacyQuantity, Money, WholeQuantity


class MixedZeroReturnRequest(MixedRefundQuoteRequest):
    operation_id: UUID4
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    reason_code: RefundReasonCode | None = None
    comment: str | None = Field(default=None, max_length=500, strict=True)


class MixedLegacyZeroReturnLine(FrozenContract):
    inventory_kind: Literal["legacy_pack"] = "legacy_pack"
    line_id: UUID
    quantity: LegacyQuantity
    offset: LegacyOffset


class MixedPhysicalZeroReturnLine(FrozenContract):
    inventory_kind: Literal["physical_unit"] = "physical_unit"
    line_id: UUID
    sale_format: SaleFormatV2
    quantity: WholeQuantity
    base_units: WholeQuantity
    offset: int = Field(strict=True, ge=0)


MixedZeroReturnLine = Annotated[
    MixedLegacyZeroReturnLine | MixedPhysicalZeroReturnLine,
    Field(discriminator="inventory_kind"),
]


class MixedZeroReturnResult(FrozenContract):
    version: Literal[1] = 1
    kind: Literal["mixed_zero_return"] = "mixed_zero_return"
    operation_id: UUID
    parent_sale_id: UUID
    tenant_id: UUID
    branch_id: UUID
    actor_id: UUID
    state: Literal["prepared_quarantine"] = "prepared_quarantine"
    currency: Literal["TJS"] = "TJS"
    total_amount: Money
    lines: tuple[MixedZeroReturnLine, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def zero_and_unique(self) -> Self:
        if self.total_amount != 0:
            raise ValueError("Quarantine without payout requires zero historical net amount")
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Return lines must be unique across inventory kinds")
        return self


class MixedZeroReturnNotFound(FrozenContract):
    state: Literal["not_found"] = "not_found"
    operation_id: UUID4
    parent_sale_id: UUID


MixedZeroReturnRead = Annotated[
    MixedZeroReturnNotFound | MixedZeroReturnResult, Field(discriminator="state")
]
