"""Internal persisted cost projection; never part of the physical or receipt response."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from typing import Literal
from uuid import UUID

from app.core.errors import BusinessRuleError
from app.domains.catalog.unit_sales import MAX_MONEY
from app.domains.inventory.unit_ledger_v2_models import UnitStockMovementV2


@dataclass(frozen=True)
class UnitStockCostSliceV2:
    movement_id: UUID
    line_id: UUID
    lot_id: UUID
    pack_ordinal_start: int
    pack_count: int
    unit_offset: int
    base_units: int
    original_pack_cost: Decimal
    amount: Decimal
    version: Literal[1] = 1
    currency: Literal["TJS"] = "TJS"


@dataclass(frozen=True)
class UnitCartDispenseCostResultV2:
    command_id: UUID
    cost_slices: tuple[UnitStockCostSliceV2, ...]
    total_amount: Decimal


def _slice(movement: UnitStockMovementV2) -> UnitStockCostSliceV2:
    if (
        movement.cost_pack_ordinal_start is None
        or movement.cost_pack_count is None
        or movement.cost_unit_offset is None
        or movement.cost_base_units is None
        or movement.cost_original_pack_cost is None
        or movement.cost_amount is None
        or movement.cost_version is None
    ):
        raise BusinessRuleError("Historical physical stock cost is unknown")
    if (
        movement.cost_version != 1
        or movement.cost_pack_ordinal_start < 1
        or movement.cost_pack_count < 1
        or movement.cost_unit_offset < 0
        or movement.cost_base_units != movement.units_taken
        or movement.units_taken < 1
    ):
        raise BusinessRuleError("Physical movement has an invalid cost snapshot")
    for amount in (movement.cost_original_pack_cost, movement.cost_amount):
        if (
            not amount.is_finite()
            or amount < 0
            or amount > MAX_MONEY
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise BusinessRuleError("Physical movement has an invalid cost amount")
    return UnitStockCostSliceV2(
        movement.id,
        movement.line_id,
        movement.lot_id,
        movement.cost_pack_ordinal_start,
        movement.cost_pack_count,
        movement.cost_unit_offset,
        movement.cost_base_units,
        movement.cost_original_pack_cost,
        movement.cost_amount,
    )


def persisted_cost_result_v2(
    command_id: UUID, movements: list[UnitStockMovementV2]
) -> UnitCartDispenseCostResultV2:
    # The original database snapshots are authoritative, including known zero.
    # An explicit context keeps totals independent of a caller's Decimal settings.
    with localcontext(Context(prec=40)):
        slices = tuple(_slice(movement) for movement in movements if movement.units_taken > 0)
        if not slices:
            raise BusinessRuleError("Physical cart has no persisted cost allocations")
        total = sum((part.amount for part in slices), Decimal("0.00"))
        if total > MAX_MONEY:
            raise BusinessRuleError("Physical cart cost exceeds the money limit")
    return UnitCartDispenseCostResultV2(command_id, slices, total)
