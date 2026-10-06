"""Pure whole-cart physical allocation; no reservation or permission to dispense."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_stock_v2 import (
    OpenedBlisterV2,
    OpenedPackV2,
    SaleFormatV2,
    UnitPackagingV2,
    UnitStockBatchV2,
    stock_availability_v2,
    to_base_units_v2,
)


class UnitCartLineV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    catalog_id: UUID
    packaging: UnitPackagingV2
    sale_format: SaleFormatV2
    quantity: int = Field(strict=True, gt=0, le=MAX_BASE_UNITS)


@dataclass(frozen=True)
class UnitStockPickV2:
    """newly_opened marks a virtual pack in this cart, not a new opening per line."""

    batch_id: UUID
    base_units: int
    closed_packs: int = 0
    opened_pack_id: UUID | None = None
    blister_numbers: tuple[int, ...] = ()
    opened_blister_number: int | None = None
    newly_opened: bool = False


@dataclass(frozen=True)
class UnitCartLineAllocationV2:
    line_id: UUID
    base_units: int
    before_batches: tuple[UnitStockBatchV2, ...]
    after_batches: tuple[UnitStockBatchV2, ...]
    picks: tuple[UnitStockPickV2, ...]


@dataclass(frozen=True)
class UnitCartStockPlanV2:
    lines: tuple[UnitCartLineAllocationV2, ...]
    allocation_order: tuple[UUID, ...]
    residual_batches: tuple[UnitStockBatchV2, ...]
    lock_batch_ids: tuple[UUID, ...]
    lock_opened_pack_ids: tuple[UUID, ...]


def _take_opened(
    batch: UnitStockBatchV2,
    pack: OpenedPackV2,
    needed: int,
    sale_format: SaleFormatV2,
    *,
    newly_opened: bool,
) -> tuple[OpenedPackV2 | None, int, list[UnitStockPickV2]]:
    """The whole opened carton obeys its step; fragments retain identities."""
    size = batch.packaging.units_per_blister
    step = batch.packaging.dispense_step
    intact = sorted(pack.intact_blisters)
    fragments = sorted(pack.opened_blisters, key=lambda item: item.number)
    loose = pack.loose_units
    picks: list[UnitStockPickV2] = []

    def record(units: int, number: int | None, *, was_intact: bool = False) -> None:
        picks.append(
            UnitStockPickV2(
                batch_id=batch.id,
                base_units=units,
                opened_pack_id=pack.id,
                blister_numbers=(number,) if was_intact and number is not None else (),
                opened_blister_number=number if not was_intact else None,
                newly_opened=newly_opened,
            )
        )

    if sale_format == "blister" and size is not None:
        count = min(len(intact), needed // size)
        numbers = tuple(intact[:count])
        if count:
            picks.append(
                UnitStockPickV2(
                    batch_id=batch.id,
                    base_units=count * size,
                    opened_pack_id=pack.id,
                    blister_numbers=numbers,
                    newly_opened=newly_opened,
                )
            )
            needed -= count * size
            intact = intact[count:]
    elif sale_format == "unit":
        total = loose + sum(item.remaining_units for item in fragments)
        if size is not None:
            total += len(intact) * size
        quota = min(needed, total // step * step)
        amount = min(quota, loose)
        if amount:
            loose -= amount
            needed -= amount
            quota -= amount
            record(amount, None)
        remaining_fragments: list[OpenedBlisterV2] = []
        for fragment in fragments:
            amount = min(quota, fragment.remaining_units)
            if amount:
                needed -= amount
                quota -= amount
                record(amount, fragment.number)
            remainder = fragment.remaining_units - amount
            if remainder:
                remaining_fragments.append(
                    OpenedBlisterV2(number=fragment.number, remaining_units=remainder)
                )
        fragments = remaining_fragments
        remaining_intact: list[int] = []
        for number in intact:
            assert size is not None
            amount = min(quota, size)
            if not amount:
                remaining_intact.append(number)
                continue
            needed -= amount
            quota -= amount
            record(amount, number, was_intact=True)
            if amount < size:
                fragments.append(OpenedBlisterV2(number=number, remaining_units=size - amount))
        intact = remaining_intact
    if not (loose or intact or fragments):
        return None, needed, picks
    return (
        OpenedPackV2(
            id=pack.id,
            opened_at=pack.opened_at,
            loose_units=loose,
            intact_blisters=tuple(intact),
            opened_blisters=tuple(sorted(fragments, key=lambda item: item.number)),
        ),
        needed,
        picks,
    )


def _take_batch(
    batch: UnitStockBatchV2,
    needed: int,
    sale_format: SaleFormatV2,
    *,
    original_closed: int,
    original_pack_ids: set[UUID],
    used_pack_ids: set[UUID],
) -> tuple[UnitStockBatchV2, int, list[UnitStockPickV2]]:
    """Original opened cartons precede virtual ones regardless of marker age.

    Virtual opened_at is a deterministic draft marker, not a real opening time.
    """
    packaging = batch.packaging
    closed = batch.closed_packs
    packs: list[OpenedPackV2] = []
    picks: list[UnitStockPickV2] = []
    for pack in sorted(
        batch.opened_packs,
        key=lambda item: (item.id not in original_pack_ids, item.opened_at, item.id),
    ):
        if sale_format == "pack" or not needed:
            packs.append(pack)
            continue
        remaining, needed, selected = _take_opened(
            batch, pack, needed, sale_format, newly_opened=pack.id not in original_pack_ids
        )
        picks.extend(selected)
        if remaining is not None:
            packs.append(remaining)

    full = min(closed, needed // packaging.units_per_pack)
    if full:
        units = full * packaging.units_per_pack
        picks.append(UnitStockPickV2(batch_id=batch.id, base_units=units, closed_packs=full))
        closed -= full
        needed -= units
    if needed and closed and sale_format != "pack":
        ordinal = original_closed - closed
        attempt = 0
        pack_id = uuid5(batch.id, f"unit-cart-v2:{ordinal}:{attempt}")
        while pack_id in used_pack_ids:
            attempt += 1
            pack_id = uuid5(batch.id, f"unit-cart-v2:{ordinal}:{attempt}")
        used_pack_ids.add(pack_id)
        size = packaging.units_per_blister
        opened = OpenedPackV2(
            id=pack_id,
            # A deterministic draft marker, never a posted opening timestamp.
            opened_at=batch.created_at,
            intact_blisters=(
                tuple(range(1, packaging.units_per_pack // size + 1)) if size is not None else ()
            ),
            loose_units=packaging.units_per_pack if size is None else 0,
        )
        remaining, needed_after, selected = _take_opened(
            batch, opened, needed, sale_format, newly_opened=True
        )
        if selected:
            closed -= 1
            needed = needed_after
            picks.extend(selected)
            if remaining is not None:
                packs.append(remaining)
    return (
        UnitStockBatchV2(
            **batch.model_dump(exclude={"closed_packs", "opened_packs"}),
            closed_packs=closed,
            opened_packs=tuple(packs),
        ),
        needed,
        picks,
    )


def _checked_inputs(
    *,
    lines: tuple[UnitCartLineV2, ...],
    batches: tuple[UnitStockBatchV2, ...],
    tenant_id: UUID,
    branch_id: UUID,
    today: date,
) -> tuple[tuple[UnitCartLineV2, ...], tuple[UnitStockBatchV2, ...], set[UUID], set[UUID]]:
    """Revalidate models and all identities before filtering physical stock."""
    if not isinstance(tenant_id, UUID) or not isinstance(branch_id, UUID):
        raise ValueError("tenant_id and branch_id must be UUIDs")
    if type(today) is not date:
        raise ValueError("today must be a calendar date")
    if not isinstance(lines, tuple) or not 0 < len(lines) <= 200:
        raise ValueError("cart must contain between 1 and 200 version 2 lines")
    if any(not isinstance(line, UnitCartLineV2) for line in lines):
        raise ValueError("version 2 cart lines are required")
    lines = tuple(UnitCartLineV2.model_validate(line.model_dump()) for line in lines)
    if len({line.id for line in lines}) != len(lines):
        raise ValueError("cart line identifiers must be unique")
    if not isinstance(batches, tuple) or any(
        not isinstance(batch, UnitStockBatchV2) for batch in batches
    ):
        raise ValueError("version 2 batch snapshots are required")
    batches = tuple(UnitStockBatchV2.model_validate(batch.model_dump()) for batch in batches)
    batch_ids: set[UUID] = set()
    pack_ids: set[UUID] = set()
    catalogs = {line.catalog_id for line in lines}
    for batch in batches:
        if (batch.tenant_id, batch.branch_id) != (tenant_id, branch_id):
            raise ValueError("stock snapshot is outside the requested scope")
        if batch.catalog_id not in catalogs:
            raise ValueError("stock catalog is outside the requested cart")
        if batch.id in batch_ids:
            raise ValueError("batch identifiers must be unique")
        batch_ids.add(batch.id)
        for pack in batch.opened_packs:
            if pack.id in pack_ids:
                raise ValueError("opened pack identifiers must be globally unique")
            pack_ids.add(pack.id)
    return lines, batches, batch_ids, pack_ids


def plan_unit_cart_stock_v2(
    *,
    lines: tuple[UnitCartLineV2, ...],
    batches: tuple[UnitStockBatchV2, ...],
    tenant_id: UUID,
    branch_id: UUID,
    today: date,
) -> UnitCartStockPlanV2:
    """Allocate pack, blister, then unit lines; return results in input order.

    All input scope and identity validation precedes exclusion or allocation.
    Failures return no partial plan. Virtual pack identifiers and timestamps
    must never be posted. A future writer must lock original coverage, recheck
    physical facts and atomically post immutable movements with real identities.
    """
    lines, batches, batch_ids, pack_ids = _checked_inputs(
        lines=lines, batches=batches, tenant_id=tenant_id, branch_id=branch_id, today=today
    )
    quantities = {
        line.id: to_base_units_v2(
            line.packaging, quantity=line.quantity, sale_format=line.sale_format
        )
        for line in lines
    }
    for catalog_id, packaging in {(line.catalog_id, line.packaging) for line in lines}:
        stock_availability_v2(
            batches=tuple(batch for batch in batches if batch.catalog_id == catalog_id),
            tenant_id=tenant_id,
            branch_id=branch_id,
            catalog_id=catalog_id,
            today=today,
            packaging=packaging,
        )
    if sum(quantities.values()) > MAX_BASE_UNITS:
        raise ValueError("cart exceeds the base unit limit")
    remaining_batches = list(batches)
    original_closed = {batch.id: batch.closed_packs for batch in batches}
    used_pack_ids = set(pack_ids)
    results: dict[UUID, UnitCartLineAllocationV2] = {}
    priority = {"pack": 0, "blister": 1, "unit": 2}
    ordered_lines = sorted(lines, key=lambda item: priority[item.sale_format])
    for line in ordered_lines:
        before = tuple(remaining_batches)
        needed = quantities[line.id]
        picks: list[UnitStockPickV2] = []
        indices = sorted(
            range(len(remaining_batches)),
            key=lambda index: (
                remaining_batches[index].expires_at,
                remaining_batches[index].created_at,
                remaining_batches[index].id,
            ),
        )
        for index in indices:
            batch = remaining_batches[index]
            if (
                not needed
                or batch.catalog_id != line.catalog_id
                or batch.packaging != line.packaging
                or batch.is_blocked
                or batch.expires_at <= today
            ):
                continue
            remaining_batches[index], needed, selected = _take_batch(
                batch,
                needed,
                line.sale_format,
                original_closed=original_closed[batch.id],
                original_pack_ids=pack_ids,
                used_pack_ids=used_pack_ids,
            )
            picks.extend(selected)
        if needed:
            raise ValueError("insufficient physical stock for cart line")
        results[line.id] = UnitCartLineAllocationV2(
            line_id=line.id,
            base_units=quantities[line.id],
            before_batches=before,
            after_batches=tuple(remaining_batches),
            picks=tuple(picks),
        )
    return UnitCartStockPlanV2(
        lines=tuple(results[line.id] for line in lines),
        allocation_order=tuple(line.id for line in ordered_lines),
        residual_batches=tuple(remaining_batches),
        lock_batch_ids=tuple(sorted(batch_ids)),
        lock_opened_pack_ids=tuple(sorted(pack_ids)),
    )
