"""Internal immutable receipt into quarantine; never a payout document."""

from decimal import Decimal, localcontext
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.domains.pos.unit_contracts import EXACT_CONTEXT, FrozenContract, Money, WholeQuantity

Offset = Annotated[int, Field(strict=True, ge=0)]


class PhysicalReturnJournalSlice(FrozenContract):
    source_movement_id: UUID
    lot_id: UUID
    source_offset: Offset
    base_units: WholeQuantity
    cost_amount: Money


class PhysicalReturnJournalLine(FrozenContract):
    line_id: UUID
    base_units: WholeQuantity
    offset: Offset
    net_amount: Money
    discount_amount: Money
    gross_amount: Money
    cost_amount: Money
    slices: tuple[PhysicalReturnJournalSlice, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def balanced(self) -> Self:
        with localcontext(EXACT_CONTEXT):
            if self.net_amount + self.discount_amount != self.gross_amount:
                raise ValueError("Return retail amounts differ")
            if sum((s.cost_amount for s in self.slices), Decimal("0.00")) != self.cost_amount:
                raise ValueError("Return slice costs differ")
        if sum(s.base_units for s in self.slices) != self.base_units:
            raise ValueError("Return slice quantities differ")
        if len({s.source_movement_id for s in self.slices}) != len(self.slices):
            raise ValueError("Return source slices must be unique")
        return self


class PhysicalReturnJournalResult(FrozenContract):
    operation_id: UUID
    parent_sale_id: UUID
    tenant_id: UUID
    branch_id: UUID
    actor_id: UUID
    state: Literal["prepared_quarantine"] = "prepared_quarantine"
    currency: Literal["TJS"] = "TJS"
    lines: tuple[PhysicalReturnJournalLine, ...] = Field(min_length=1, max_length=200)
    total_amount: Money
    total_cost_amount: Money

    @model_validator(mode="after")
    def balanced(self) -> Self:
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Return lines must be unique")
        with localcontext(EXACT_CONTEXT):
            if sum((line.net_amount for line in self.lines), Decimal("0.00")) != self.total_amount:
                raise ValueError("Return total differs")
            if (
                sum((line.cost_amount for line in self.lines), Decimal("0.00"))
                != self.total_cost_amount
            ):
                raise ValueError("Return cost total differs")
        return self
