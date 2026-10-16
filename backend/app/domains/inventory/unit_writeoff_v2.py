"""Pure removal of explicitly selected physical stock, without posting authority.

Unlike sales, damaged stock may contain fewer units than the dispensing step.
Expiry and blocking do not invalidate a physical calculation: the future writer
must authorize the operation, lock and recheck facts, and journal historical cost
atomically. This module does not reserve stock or establish idempotency or RLS.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_stock_v2 import (
    OpenedBlisterV2,
    OpenedPackV2,
    UnitPackagingV2,
    UnitStockBatchV2,
    _opened_units,
)


class PhysicalBlisterWriteOffV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int = Field(strict=True, ge=1)
    units: int = Field(strict=True, gt=0, le=MAX_BASE_UNITS)


class PhysicalPackWriteOffV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pack_id: UUID
    blisters: tuple[PhysicalBlisterWriteOffV2, ...] = ()
    loose_units: int = Field(default=0, strict=True, ge=0, le=MAX_BASE_UNITS)


class PhysicalWriteOffSelectionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    closed_packs: int = Field(default=0, strict=True, ge=0, le=MAX_BASE_UNITS)
    opened_packs: tuple[PhysicalPackWriteOffV2, ...] = ()


@dataclass(frozen=True)
class PhysicalWriteOffPlanV2:
    before: UnitStockBatchV2
    after: UnitStockBatchV2
    base_units: int


def _blister_remainder(
    pack: OpenedPackV2,
    selection: PhysicalPackWriteOffV2,
    size: int,
) -> tuple[tuple[int, ...], tuple[OpenedBlisterV2, ...]]:
    if selection.loose_units:
        raise ValueError("known composition requires explicit blister identities")
    available = {number: size for number in pack.intact_blisters}
    available.update({item.number: item.remaining_units for item in pack.opened_blisters})
    numbers: set[int] = set()
    for item in selection.blisters:
        if item.number in numbers:
            raise ValueError("write-off blister identifiers must be unique")
        numbers.add(item.number)
        if item.number not in available or item.units > available[item.number]:
            raise ValueError("write-off exceeds the selected blister remainder")
        available[item.number] -= item.units
    intact = tuple(number for number, units in sorted(available.items()) if units == size)
    opened = tuple(
        OpenedBlisterV2(number=number, remaining_units=units)
        for number, units in sorted(available.items())
        if 0 < units < size
    )
    return intact, opened


def _pack_remainder(
    pack: OpenedPackV2,
    selection: PhysicalPackWriteOffV2,
    packaging: UnitPackagingV2,
) -> OpenedPackV2 | None:
    if not selection.blisters and not selection.loose_units:
        raise ValueError("each selected pack requires a positive write-off")
    intact: tuple[int, ...] = ()
    opened: tuple[OpenedBlisterV2, ...] = ()
    loose = 0
    if packaging.units_per_blister is None:
        if selection.blisters:
            raise ValueError("unknown composition cannot identify blisters")
        if selection.loose_units > pack.loose_units:
            raise ValueError("write-off exceeds the selected loose remainder")
        loose = pack.loose_units - selection.loose_units
    else:
        intact, opened = _blister_remainder(pack, selection, packaging.units_per_blister)
    if not intact and not opened and not loose:
        return None
    return OpenedPackV2(
        id=pack.id,
        opened_at=pack.opened_at,
        intact_blisters=intact,
        opened_blisters=opened,
        loose_units=loose,
    )


def plan_physical_writeoff_v2(
    *,
    batch: UnitStockBatchV2,
    tenant_id: UUID,
    branch_id: UUID,
    selection: PhysicalWriteOffSelectionV2,
) -> PhysicalWriteOffPlanV2:
    """Calculate one batch without selecting replacement stock or opening cartons.

    Sealed packs are selected as a count. Opened packs and blisters are addressed
    by their existing identities. The writer must resolve sealed ordinal ranges
    from locked journal facts; this preview never invents a physical identity.
    """
    if not isinstance(batch, UnitStockBatchV2) or not isinstance(
        selection, PhysicalWriteOffSelectionV2
    ):
        raise ValueError("version-two physical stock and write-off selection are required")
    if not isinstance(tenant_id, UUID) or not isinstance(branch_id, UUID):
        raise ValueError("explicit tenant and branch identifiers are required")
    # Revalidate internal callers too: model_copy/model_construct bypass validation.
    batch = UnitStockBatchV2.model_validate(batch.model_dump())
    selection = PhysicalWriteOffSelectionV2.model_validate(selection.model_dump())
    if (batch.tenant_id, batch.branch_id) != (tenant_id, branch_id):
        raise ValueError("stock snapshot is outside the requested scope")
    if selection.closed_packs > batch.closed_packs:
        raise ValueError("write-off exceeds the sealed pack count")
    selected = {pack.pack_id: pack for pack in selection.opened_packs}
    if len(selected) != len(selection.opened_packs):
        raise ValueError("write-off pack identifiers must be unique")
    available_ids = {pack.id for pack in batch.opened_packs}
    if not selected.keys() <= available_ids:
        raise ValueError("write-off pack does not belong to this stock snapshot")
    remaining: list[OpenedPackV2] = []
    units = selection.closed_packs * batch.packaging.units_per_pack
    for pack in sorted(batch.opened_packs, key=lambda item: item.id):
        choice = selected.get(pack.id)
        if choice is None:
            remaining.append(pack)
            continue
        after = _pack_remainder(pack, choice, batch.packaging)
        units += _opened_units(pack, batch.packaging)
        if after is not None:
            units -= _opened_units(after, batch.packaging)
            remaining.append(after)
    if not units:
        raise ValueError("write-off requires a positive quantity")
    after_batch = UnitStockBatchV2.model_validate(
        {
            **batch.model_dump(),
            "closed_packs": batch.closed_packs - selection.closed_packs,
            "opened_packs": tuple(remaining),
        }
    )
    return PhysicalWriteOffPlanV2(before=batch, after=after_batch, base_units=units)
