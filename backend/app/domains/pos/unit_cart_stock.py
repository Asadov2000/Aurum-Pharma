"""Pure whole-cart physical previews; synthetic pack IDs must never be posted.

This planner neither reserves stock nor authorizes checkout. A write must lock
the real stock and replan it; virtual remainders only prevent double allocation
inside this preview. Whole packs are reserved before unit lines so an earlier
unit line cannot consume the only unopened pack needed by a later pack line.
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domains.inventory.unit_stock import (
    OpenedPack,
    UnitStockBatch,
    UnitStockPlan,
    plan_unit_stock,
)
from app.domains.pos.unit_cart_quote import UnitDraftQuoteLineV2


@dataclass(frozen=True)
class UnitCartStockLine:
    item_id: UUID
    plan: UnitStockPlan


def _validate_snapshot_ids(
    batches: tuple[UnitStockBatch, ...], *, tenant_id: UUID, branch_id: UUID
) -> set[UUID]:
    if len({batch.id for batch in batches}) != len(batches):
        raise ValueError("batch identifiers must be unique")
    pack_ids: set[UUID] = set()
    for batch in batches:
        if (batch.tenant_id, batch.branch_id) != (tenant_id, branch_id):
            raise ValueError("stock snapshot is outside the requested scope")
        for pack in batch.opened_packs:
            if pack.id in pack_ids:
                raise ValueError("opened pack identifiers must be globally unique")
            pack_ids.add(pack.id)
    return pack_ids


def plan_unit_cart_stock(
    *,
    lines: tuple[UnitDraftQuoteLineV2, ...],
    batches: tuple[UnitStockBatch, ...],
    tenant_id: UUID,
    branch_id: UUID,
    today: date,
) -> tuple[UnitCartStockLine, ...]:
    """Allocate whole packs first, preserving input order within each format.

    Pick IDs may refer to synthetic opened packs. Lock IDs contain only the
    original physical snapshot IDs, including depleted/excluded snapshots;
    they remain descriptive coverage, never instructions for database writes.
    A future checkout must repeat the full plan under locks and post packs first.
    The item identifier connects this planning order to the unchanged quote.
    """
    if not isinstance(lines, tuple) or not isinstance(batches, tuple):
        raise ValueError("cart lines and stock snapshots must be tuples")
    if not isinstance(tenant_id, UUID) or not isinstance(branch_id, UUID):
        raise ValueError("stock scope identifiers must be UUIDs")
    if type(today) is not date:
        raise ValueError("stock preview requires a calendar date")
    checked_lines = tuple(UnitDraftQuoteLineV2.model_validate(line) for line in lines)
    checked_batches = tuple(UnitStockBatch.model_validate(batch.model_dump()) for batch in batches)
    if len({line.id for line in checked_lines}) != len(checked_lines):
        raise ValueError("cart item identifiers must be unique")
    pack_ids = _validate_snapshot_ids(checked_batches, tenant_id=tenant_id, branch_id=branch_id)
    virtual = {batch.id: batch for batch in checked_batches}
    results: list[UnitCartStockLine] = []
    for line in sorted(checked_lines, key=lambda line: line.sale_format != "pack"):
        original = tuple(batch for batch in checked_batches if batch.catalog_id == line.catalog_id)
        stock = tuple(virtual[batch.id] for batch in original)
        plan = plan_unit_stock(
            batches=stock,
            tenant_id=tenant_id,
            branch_id=branch_id,
            catalog_id=line.catalog_id,
            today=today,
            sale_format=line.sale_format,
            quantity=line.quantity,
            packaging=line.packaging,
        )
        plan = replace(
            plan,
            lock_batch_ids=tuple(sorted(batch.id for batch in original)),
            lock_opened_pack_ids=tuple(
                sorted(pack.id for batch in original for pack in batch.opened_packs)
            ),
        )
        results.append(UnitCartStockLine(item_id=line.id, plan=plan))
        for pick in plan.picks:
            batch = virtual[pick.batch_id]
            remainders = {take.pack_id: take.remaining_units for take in pick.opened_packs}
            opened = tuple(
                OpenedPack.model_validate(
                    pack.model_dump()
                    | {"remaining_units": remainders.get(pack.id, pack.remaining_units)}
                )
                for pack in batch.opened_packs
                if remainders.get(pack.id, pack.remaining_units) > 0
            )
            if pick.new_opened_remainder_units:
                synthetic_id = uuid5(
                    NAMESPACE_URL,
                    f"aurum-cart-preview-pack:{tenant_id}:{branch_id}:{line.id}:{batch.id}",
                )
                if synthetic_id in pack_ids:
                    raise ValueError("synthetic opened pack identifier collides with the snapshot")
                pack_ids.add(synthetic_id)
                opened_at = max(
                    datetime.combine(today, time.min, tzinfo=UTC),
                    batch.created_at,
                    *(pack.opened_at for pack in batch.opened_packs),
                ) + timedelta(microseconds=1)
                opened += (
                    OpenedPack(
                        id=synthetic_id,
                        opened_at=opened_at,
                        remaining_units=pick.new_opened_remainder_units,
                    ),
                )
            virtual[batch.id] = UnitStockBatch.model_validate(
                batch.model_dump()
                | {
                    "closed_packs": (
                        batch.closed_packs - pick.closed_packs_taken - pick.new_packs_opened
                    ),
                    "opened_packs": opened,
                }
            )
    return tuple(results)
