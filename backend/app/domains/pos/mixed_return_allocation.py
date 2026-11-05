"""Pure return allocation from immutable mixed checkout v4 retail facts.

This does not authorize a return, pay money, admit stock or prove packaging
condition. A future writer must authenticate the historical source, enforce
tenant/branch access, lock trusted persisted cursors, prevent repeated commands
and receive both kinds of goods into quarantine in one transaction. Cursors
must never come from a client. Existing mixed-refund write prohibitions remain.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID

from app.domains.inventory.unit_stock_v2 import SaleFormatV2, to_base_units_v2
from app.domains.pos.mixed_documents import LegacyResultLineV4, MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalResultLineV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.pos.unit_money import allocate_unit_amount


@dataclass(frozen=True)
class LegacyReturnSelection:
    line_id: UUID
    quantity: Decimal


MixedReturnSelection = LegacyReturnSelection | PhysicalReturnSelection


@dataclass(frozen=True)
class LegacyReturnAllocation:
    line_id: UUID
    position: int
    quantity: Decimal
    offset: Decimal
    original_quantity: Decimal
    original_net_amount: Decimal
    net_amount: Decimal
    discount_amount: Decimal
    gross_amount: Decimal
    inventory_kind: Literal["legacy_pack"] = field(default="legacy_pack", init=False)


@dataclass(frozen=True)
class MixedPhysicalReturnAllocation:
    line_id: UUID
    position: int
    sale_format: SaleFormatV2
    quantity: int
    base_units: int
    offset: int
    original_base_units: int
    original_net_amount: Decimal
    net_amount: Decimal
    discount_amount: Decimal
    gross_amount: Decimal
    inventory_kind: Literal["physical_unit"] = field(default="physical_unit", init=False)


MixedReturnAllocation = LegacyReturnAllocation | MixedPhysicalReturnAllocation


@dataclass(frozen=True)
class MixedReturnPlan:
    version: Literal[1]
    tenant_id: UUID
    branch_id: UUID
    sale_id: UUID
    parent_operation_id: UUID
    currency: Literal["TJS"]
    lines: tuple[MixedReturnAllocation, ...]
    total_amount: Decimal


def _legacy_ticks(value: object, maximum: Decimal, *, allow_zero: bool) -> int:
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or value < 0
        or value > maximum
        or (value == 0 and not allow_zero)
    ):
        raise ValueError("legacy quantity must be a Decimal within the original range")
    with localcontext(EXACT_CONTEXT):
        canonical = value.quantize(Decimal("0.001"))
        if value != canonical:
            raise ValueError("legacy quantity must contain whole thousandths of a pack")
        return int(canonical.scaleb(3))


def _legacy_cursors(
    lines: Mapping[UUID, LegacyResultLineV4], values: Mapping[UUID, Decimal]
) -> dict[UUID, int]:
    if not isinstance(values, Mapping):
        raise ValueError("trusted legacy cursors must be a mapping")
    cursors: dict[UUID, int] = {}
    for line_id, quantity in dict(values).items():
        line = lines.get(line_id)
        if type(line_id) is not UUID or line is None:
            raise ValueError("legacy cursor must identify an original legacy line")
        cursors[line_id] = _legacy_ticks(quantity, line.quantity, allow_zero=True)
    return cursors


def _physical_cursors(
    lines: Mapping[UUID, PhysicalResultLineV3], values: Mapping[UUID, int]
) -> dict[UUID, int]:
    if not isinstance(values, Mapping):
        raise ValueError("trusted physical cursors must be a mapping")
    cursors = dict(values)
    for line_id, offset in cursors.items():
        line = lines.get(line_id)
        if (
            type(line_id) is not UUID
            or line is None
            or type(offset) is not int
            or not 0 <= offset <= line.base_units
            or offset % line.packaging.dispense_step
        ):
            raise ValueError("physical cursor must belong to an original line and valid range")
    return cursors


def _legacy_amount(amount: Decimal, *, total: int, offset: int, quantity: int) -> Decimal:
    # NUMERIC(14,3) can exceed the physical allocator's base-unit limit after
    # scaling. Keep legacy thousandths separate from physical units. All money
    # has already been revalidated as whole cents by the source v4 contract.
    with localcontext(EXACT_CONTEXT):
        cents = int(amount.scaleb(2))
        allocated = cents * (offset + quantity) // total - cents * offset // total
        return Decimal(allocated).scaleb(-2)


def _allocate_legacy(
    line: LegacyResultLineV4, selection: LegacyReturnSelection, offset: int
) -> LegacyReturnAllocation:
    total = _legacy_ticks(line.quantity, line.quantity, allow_zero=False)
    quantity = _legacy_ticks(selection.quantity, line.quantity, allow_zero=False)
    if quantity > total - offset:
        raise ValueError("legacy return quantity exceeds the remaining original quantity")
    net = _legacy_amount(line.net_amount, total=total, offset=offset, quantity=quantity)
    discount = _legacy_amount(line.discount_amount, total=total, offset=offset, quantity=quantity)
    with localcontext(EXACT_CONTEXT):
        return LegacyReturnAllocation(
            line_id=line.id,
            position=line.position,
            quantity=Decimal(quantity).scaleb(-3),
            offset=Decimal(offset).scaleb(-3),
            original_quantity=line.quantity,
            original_net_amount=line.net_amount,
            net_amount=net,
            discount_amount=discount,
            gross_amount=net + discount,
        )


def _allocate_physical(
    line: PhysicalResultLineV3, selection: PhysicalReturnSelection, offset: int
) -> MixedPhysicalReturnAllocation:
    units = to_base_units_v2(
        line.packaging, quantity=selection.quantity, sale_format=selection.sale_format
    )
    net = allocate_unit_amount(
        line.net_amount, total_units=line.base_units, offset=offset, quantity=units
    ).amount
    discount = allocate_unit_amount(
        line.discount_amount, total_units=line.base_units, offset=offset, quantity=units
    ).amount
    with localcontext(EXACT_CONTEXT):
        gross = net + discount
    return MixedPhysicalReturnAllocation(
        line_id=line.id,
        position=line.position,
        sale_format=selection.sale_format,
        quantity=selection.quantity,
        base_units=units,
        offset=offset,
        original_base_units=line.base_units,
        original_net_amount=line.net_amount,
        net_amount=net,
        discount_amount=discount,
        gross_amount=gross,
    )


def plan_mixed_return(
    checkout: MixedCheckoutResultV4,
    selections: tuple[MixedReturnSelection, ...],
    *,
    returned_legacy_quantities: Mapping[UUID, Decimal],
    returned_base_units: Mapping[UUID, int],
) -> MixedReturnPlan:
    """Allocate original net and discount independently using cumulative floors.

    Legacy counting uses .001 packs; physical counting uses the saved packaging
    and dispense step. Missing trusted cursors mean no previous returns. Every
    supplied cursor is checked, including unselected lines. The result follows
    original receipt order and may select one stock kind or total zero TJS.
    This v1 mixed policy does not change the old legacy HALF_UP refund writer.
    """
    if type(checkout) is not MixedCheckoutResultV4:
        raise ValueError("an original mixed checkout v4 is required")
    original = MixedCheckoutResultV4.model_validate(checkout.model_dump(mode="python"))
    if type(selections) is not tuple or not 1 <= len(selections) <= 200:
        raise ValueError("return selections must contain between 1 and 200 lines")
    legacy_lines = {
        line.id: line for line in original.lines if isinstance(line, LegacyResultLineV4)
    }
    physical_lines = {
        line.id: line for line in original.lines if isinstance(line, PhysicalResultLineV3)
    }
    legacy_cursors = _legacy_cursors(legacy_lines, returned_legacy_quantities)
    physical_cursors = _physical_cursors(physical_lines, returned_base_units)
    seen: set[UUID] = set()
    allocated: list[MixedReturnAllocation] = []
    for selection in selections:
        if type(selection) not in (LegacyReturnSelection, PhysicalReturnSelection):
            raise ValueError("a typed mixed return selection is required")
        if type(selection.line_id) is not UUID or selection.line_id in seen:
            raise ValueError("return selections must identify unique original lines")
        seen.add(selection.line_id)
        if isinstance(selection, LegacyReturnSelection):
            legacy = legacy_lines.get(selection.line_id)
            if legacy is None:
                raise ValueError("legacy return selection must identify an original legacy line")
            allocated.append(
                _allocate_legacy(legacy, selection, legacy_cursors.get(selection.line_id, 0))
            )
        else:
            physical = physical_lines.get(selection.line_id)
            if physical is None:
                raise ValueError(
                    "physical return selection must identify an original physical line"
                )
            allocated.append(
                _allocate_physical(physical, selection, physical_cursors.get(selection.line_id, 0))
            )
    allocated.sort(key=lambda line: line.position)
    with localcontext(EXACT_CONTEXT):
        total = sum((line.net_amount for line in allocated), Decimal("0.00"))
    return MixedReturnPlan(
        version=1,
        tenant_id=original.tenant_id,
        branch_id=original.branch_id,
        sale_id=original.sale_id,
        parent_operation_id=original.operation_id,
        currency="TJS",
        lines=tuple(allocated),
        total_amount=total,
    )
