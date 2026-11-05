"""Immutable mixed quarantine receipt; preparation never authorizes payout."""

from decimal import Decimal, localcontext
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BeforeValidator, Field, PlainSerializer, model_validator

from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.physical_return_journal_schemas import PhysicalReturnJournalSlice
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    MAX_LEGACY_QUANTITY,
    FrozenContract,
    LegacyQuantity,
    Money,
    Position,
    WholeQuantity,
    _decimal,
)


def _legacy_offset(value: object) -> Decimal:
    offset = _decimal(value)
    if not offset.is_finite() or not 0 <= offset <= MAX_LEGACY_QUANTITY:
        raise ValueError("Legacy offset is outside NUMERIC(14,3)")
    with localcontext(EXACT_CONTEXT):
        canonical = offset.quantize(Decimal("0.001"))
    if canonical != offset:
        raise ValueError("Legacy offset must contain whole thousandths")
    return canonical


LegacyOffset = Annotated[
    Decimal,
    BeforeValidator(_legacy_offset),
    PlainSerializer(lambda value: format(value, ".3f"), return_type=str, when_used="json"),
]


class LegacyReturnJournalLine(FrozenContract):
    inventory_kind: Literal["legacy_pack"] = "legacy_pack"
    line_id: UUID
    position: Position
    batch_id: UUID
    quantity: LegacyQuantity
    offset: LegacyOffset
    net_amount: Money
    discount_amount: Money
    gross_amount: Money

    @model_validator(mode="after")
    def balanced(self) -> Self:
        with localcontext(EXACT_CONTEXT):
            if self.net_amount + self.discount_amount != self.gross_amount:
                raise ValueError("Return retail amounts differ")
        return self


class MixedPhysicalReturnJournalLine(FrozenContract):
    inventory_kind: Literal["physical_unit"] = "physical_unit"
    line_id: UUID
    position: Position
    sale_format: SaleFormatV2
    quantity: WholeQuantity
    base_units: WholeQuantity
    offset: Annotated[int, Field(strict=True, ge=0)]
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


MixedReturnJournalLine = Annotated[
    LegacyReturnJournalLine | MixedPhysicalReturnJournalLine,
    Field(discriminator="inventory_kind"),
]


class MixedReturnJournalResult(FrozenContract):
    schema_version: Literal[1] = 1
    document_kind: Literal["mixed_return_quarantine"] = "mixed_return_quarantine"
    operation_id: UUID
    parent_sale_id: UUID
    tenant_id: UUID
    branch_id: UUID
    actor_id: UUID
    state: Literal["prepared_quarantine"] = "prepared_quarantine"
    currency: Literal["TJS"] = "TJS"
    lines: tuple[MixedReturnJournalLine, ...] = Field(min_length=1, max_length=200)
    total_amount: Money
    physical_cost_amount: Money

    @model_validator(mode="after")
    def balanced(self) -> Self:
        positions = tuple(line.position for line in self.lines)
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Return lines must be unique")
        if positions != tuple(sorted(set(positions))):
            raise ValueError("Return lines must follow original receipt positions")
        with localcontext(EXACT_CONTEXT):
            if sum((line.net_amount for line in self.lines), Decimal("0.00")) != self.total_amount:
                raise ValueError("Return total differs")
            if (
                sum(
                    (
                        line.cost_amount
                        for line in self.lines
                        if isinstance(line, MixedPhysicalReturnJournalLine)
                    ),
                    Decimal("0.00"),
                )
                != self.physical_cost_amount
            ):
                raise ValueError("Physical return cost total differs")
        return self
