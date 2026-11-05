"""Immutable mixed cash refund documents, separate from sale receipts."""

from decimal import Decimal, localcontext
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domains.pos.mixed_return_journal_schemas import LegacyOffset
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundLineV3
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    FrozenContract,
    LegacyQuantity,
    Money,
    Position,
)


class MixedLegacyCashRefundLineV4(FrozenContract):
    inventory_kind: Literal["legacy_pack"] = "legacy_pack"
    original_line_id: UUID
    position: Position
    original_position: Position
    name: str = Field(strict=True, min_length=1, max_length=500)
    original_retail_unit_price: Money
    quantity: LegacyQuantity
    offset: LegacyOffset
    gross_amount: Money
    discount_amount: Money
    net_amount: Money

    @model_validator(mode="after")
    def balanced(self) -> Self:
        with localcontext(EXACT_CONTEXT):
            if self.gross_amount != self.net_amount + self.discount_amount:
                raise ValueError("Refund allocation differs")
        return self


class MixedPhysicalCashRefundLineV4(PhysicalCashRefundLineV3):
    inventory_kind: Literal["physical_unit"] = "physical_unit"
    original_position: Position


MixedCashRefundLineV4 = Annotated[
    MixedLegacyCashRefundLineV4 | MixedPhysicalCashRefundLineV4,
    Field(discriminator="inventory_kind"),
]


class MixedCashRefundReceiptV4(FrozenContract):
    schema_version: Literal[4] = 4
    document_kind: Literal["mixed_cash_refund_receipt"] = "mixed_cash_refund_receipt"
    parent_sale_id: UUID
    parent_receipt_number: str = Field(strict=True, min_length=1, max_length=100)
    receipt_number: str = Field(strict=True, min_length=1, max_length=100)
    receipt_sequence: Position
    completed_at: AwareDatetime
    is_test: bool = Field(strict=True)
    lines: tuple[MixedCashRefundLineV4, ...] = Field(min_length=1, max_length=200)
    payment_method: Literal["cash"] = "cash"
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def balanced(self) -> Self:
        if self.is_test:
            raise ValueError("Mixed cash refund requires a real sale")
        if tuple(line.position for line in self.lines) != tuple(range(1, len(self.lines) + 1)):
            raise ValueError("Refund positions differ")
        original_positions = tuple(line.original_position for line in self.lines)
        if original_positions != tuple(sorted(set(original_positions))):
            raise ValueError("Refund source order differs")
        if len({line.original_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Refund source lines must be unique")
        with localcontext(EXACT_CONTEXT):
            if sum((line.net_amount for line in self.lines), Decimal("0.00")) != self.total_amount:
                raise ValueError("Refund receipt total differs")
        if self.total_amount <= 0:
            raise ValueError("Cash refund must be positive")
        return self


class MixedCashRefundResultV4(FrozenContract):
    schema_version: Literal[4] = 4
    document_kind: Literal["mixed_cash_refund"] = "mixed_cash_refund"
    operation_id: UUID
    parent_sale_id: UUID
    sale_id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    actor_id: UUID
    payment_id: UUID
    total_amount: Money
    # Legacy checkout never saved cost: this is only the known physical portion.
    physical_cost_amount: Money
    receipt: MixedCashRefundReceiptV4

    @model_validator(mode="after")
    def bound_receipt(self) -> Self:
        if (
            self.receipt.parent_sale_id != self.parent_sale_id
            or self.receipt.total_amount != self.total_amount
        ):
            raise ValueError("Refund receipt identity differs")
        if self.payment_id != self.operation_id:
            raise ValueError("Refund payment identity differs")
        return self
