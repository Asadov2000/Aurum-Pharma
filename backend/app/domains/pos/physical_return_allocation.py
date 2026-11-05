"""Pure refund arithmetic for immutable physical checkout v3 snapshots.

This is not authorization, cash payout, stock admission or proof of intact
packaging. The future writer must lock trusted persisted return cursors, enforce
tenant/branch access and idempotency, and place received goods in quarantine.
Never populate these cursors from a client or today's catalog configuration.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID

from app.domains.inventory.unit_stock_v2 import SaleFormatV2, to_base_units_v2
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.pos.unit_money import allocate_unit_amount


@dataclass(frozen=True)
class PhysicalReturnSelection:
    line_id: UUID
    sale_format: SaleFormatV2
    quantity: int


@dataclass(frozen=True)
class PhysicalReturnAllocation:
    line_id: UUID
    sale_format: SaleFormatV2
    quantity: int
    base_units: int
    offset: int
    original_net_amount: Decimal
    net_amount: Decimal
    discount_amount: Decimal
    gross_amount: Decimal


@dataclass(frozen=True)
class PhysicalReturnPlan:
    version: Literal[1]
    tenant_id: UUID
    branch_id: UUID
    sale_id: UUID
    parent_operation_id: UUID
    currency: Literal["TJS"]
    lines: tuple[PhysicalReturnAllocation, ...]
    total_amount: Decimal


def plan_physical_return(
    checkout: PhysicalCheckoutResultV3,
    selections: tuple[PhysicalReturnSelection, ...],
    *,
    returned_base_units: Mapping[UUID, int],
) -> PhysicalReturnPlan:
    """Allocate consecutive parts of original net and discount in whole cents.

    Formats describe counting by the saved composition, not the condition of
    the goods received. Partial refund parts may be worth zero cents. The final
    part closes the original amount exactly, without intermediate rounding.
    Missing trusted cursors mean zero prior returns; unknown cursors fail closed.
    """
    if type(checkout) is not PhysicalCheckoutResultV3:
        raise ValueError("an original physical checkout v3 is required")
    original = PhysicalCheckoutResultV3.model_validate(checkout.model_dump(mode="python"))
    if type(selections) is not tuple or not 1 <= len(selections) <= 200:
        raise ValueError("return selections must contain between 1 and 200 lines")
    originals = {line.id: line for line in original.lines}
    cursors = dict(returned_base_units)
    for line_id, offset in cursors.items():
        line = originals.get(line_id)
        if (
            type(line_id) is not UUID
            or line is None
            or type(offset) is not int
            or not 0 <= offset <= line.base_units
        ):
            raise ValueError("return cursor must belong to an original line and range")
    seen: set[UUID] = set()
    allocated: list[PhysicalReturnAllocation] = []
    for selection in selections:
        if type(selection) is not PhysicalReturnSelection or type(selection.line_id) is not UUID:
            raise ValueError("a typed physical return selection is required")
        line = originals.get(selection.line_id)
        if line is None or selection.line_id in seen:
            raise ValueError("return selections must identify unique original lines")
        seen.add(selection.line_id)
        units = to_base_units_v2(
            line.packaging, quantity=selection.quantity, sale_format=selection.sale_format
        )
        offset = cursors.get(line.id, 0)
        net = allocate_unit_amount(
            line.net_amount, total_units=line.base_units, offset=offset, quantity=units
        ).amount
        discount = allocate_unit_amount(
            line.discount_amount, total_units=line.base_units, offset=offset, quantity=units
        ).amount
        with localcontext(EXACT_CONTEXT):
            gross = net + discount
        allocated.append(
            PhysicalReturnAllocation(
                line_id=line.id,
                sale_format=selection.sale_format,
                quantity=selection.quantity,
                base_units=units,
                offset=offset,
                original_net_amount=line.net_amount,
                net_amount=net,
                discount_amount=discount,
                gross_amount=gross,
            )
        )
    with localcontext(EXACT_CONTEXT):
        total = sum((line.net_amount for line in allocated), Decimal("0.00"))
    return PhysicalReturnPlan(
        version=1,
        tenant_id=original.tenant_id,
        branch_id=original.branch_id,
        sale_id=original.sale_id,
        parent_operation_id=original.operation_id,
        currency="TJS",
        lines=tuple(allocated),
        total_amount=total,
    )
