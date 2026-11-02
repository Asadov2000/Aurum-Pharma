"""Pure movement oracle for native physical cash; SQL remains the posting authority.

The oracle contains no purchase costs, sealed ordinals or execution permission.
New pack identifiers remain planner aliases until the SQL allocator maps them.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import UUID4, Field, model_validator

from app.core.errors import BusinessRuleError
from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2, UnitCartStockPlanV2
from app.domains.inventory.unit_stock_v2 import OpenedBlisterV2, OpenedPackV2
from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftV1
from app.domains.pos.edge_retail_cash_inputs import MAX_INPUT_BYTES
from app.domains.pos.edge_retail_cash_repricing import check_edge_retail_cash_draft
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_contracts import FrozenContract

MAX_NATIVE_MOVEMENTS = 8192
type NativePhysicalMovementKind = Literal["pack_take", "pack_open", "blister_take", "unit_take"]
type BlisterNumber = Annotated[int, Field(strict=True, ge=1, le=10_000)]


class NativePhysicalPackStateV1(FrozenContract):
    intact_blisters: tuple[BlisterNumber, ...] = Field(max_length=10_000)
    opened_blisters: tuple[OpenedBlisterV2, ...] = Field(max_length=10_000)
    loose_units: int = Field(strict=True, ge=0, le=10_000)

    @model_validator(mode="after")
    def ordered_unique_blisters(self) -> Self:
        fragments = tuple(item.number for item in self.opened_blisters)
        if self.intact_blisters != tuple(sorted(set(self.intact_blisters))):
            raise ValueError("native pack intact blisters must be unique and ordered")
        if fragments != tuple(sorted(set(fragments))):
            raise ValueError("native pack fragments must be unique and ordered")
        if set(self.intact_blisters) & set(fragments):
            raise ValueError("native pack blister cannot be intact and opened")
        if self.loose_units and (self.intact_blisters or self.opened_blisters):
            raise ValueError("native pack cannot mix loose and identified blister stock")
        return self


class NativePhysicalCashMovementV1(FrozenContract):
    id: UUID4
    line_id: UUID
    lot_id: UUID
    pack_id: UUID | None
    kind: NativePhysicalMovementKind
    entry_order: int = Field(strict=True, ge=0, lt=MAX_NATIVE_MOVEMENTS)
    closed_delta: int = Field(strict=True, ge=-MAX_BASE_UNITS, le=0)
    units_taken: int = Field(strict=True, ge=0, le=MAX_BASE_UNITS)
    before_state: NativePhysicalPackStateV1 | None
    after_state: NativePhysicalPackStateV1 | None

    @model_validator(mode="after")
    def exact_movement_shape(self) -> Self:
        if self.kind == "pack_take":
            if (
                self.pack_id is not None
                or self.closed_delta >= 0
                or self.units_taken <= 0
                or self.before_state is not None
                or self.after_state is not None
            ):
                raise ValueError("native sealed movement has invalid fields")
        elif self.kind == "pack_open":
            if (
                self.pack_id is None
                or self.closed_delta != -1
                or self.units_taken != 0
                or self.before_state is not None
                or self.after_state is None
            ):
                raise ValueError("native opening movement has invalid fields")
        elif (
            self.pack_id is None
            or self.closed_delta != 0
            or self.units_taken <= 0
            or self.before_state is None
            or self.after_state is None
        ):
            raise ValueError("native opened movement has invalid fields")
        return self


class NativePhysicalCashPlanV1(FrozenContract):
    schema_version: Literal[1] = 1
    quote: MixedCartQuoteV2
    stock_request_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    movements: tuple[NativePhysicalCashMovementV1, ...] = Field(
        min_length=1, max_length=MAX_NATIVE_MOVEMENTS
    )

    @model_validator(mode="after")
    def complete_physical_oracle(self) -> Self:
        if any(not isinstance(line, PhysicalUnitDraftQuoteLineV2) for line in self.quote.lines):
            raise ValueError("native physical plan cannot contain legacy positions")
        if len({movement.id for movement in self.movements}) != len(self.movements):
            raise ValueError("native movement identifiers must be unique")
        if tuple(movement.entry_order for movement in self.movements) != tuple(
            range(len(self.movements))
        ):
            raise ValueError("native movement order must be contiguous")
        expected = {
            line.id: line.base_units
            for line in self.quote.lines
            if isinstance(line, PhysicalUnitDraftQuoteLineV2)
        }
        totals: dict[UUID, int] = {}
        for movement in self.movements:
            totals[movement.line_id] = totals.get(movement.line_id, 0) + movement.units_taken
        if totals != expected:
            raise ValueError("native movements must cover every physical position exactly")
        return self


def _movement_oracle(
    plan: UnitCartStockPlanV2, lines: tuple[UnitCartLineV2, ...]
) -> tuple[NativePhysicalCashMovementV1, ...]:
    # Shared pure formatters preserve the established pack-state representation.
    from app.domains.inventory.unit_dispensing_v2_contract import (
        full_physical_pack_state,
        physical_pack_state,
    )

    line_by_id = {line.id: line for line in lines}
    allocation_by_id = {allocation.line_id: allocation for allocation in plan.lines}
    opened_aliases: set[UUID] = set()
    movements: list[NativePhysicalCashMovementV1] = []

    def state(pack: OpenedPackV2 | None) -> NativePhysicalPackStateV1:
        return NativePhysicalPackStateV1.model_validate(physical_pack_state(pack))

    def full_state(line: UnitCartLineV2) -> NativePhysicalPackStateV1:
        return NativePhysicalPackStateV1.model_validate(full_physical_pack_state(line.packaging))

    def post(
        *,
        line_id: UUID,
        lot_id: UUID,
        kind: NativePhysicalMovementKind,
        units_taken: int,
        closed_delta: int = 0,
        pack_id: UUID | None = None,
        before: NativePhysicalPackStateV1 | None = None,
        after: NativePhysicalPackStateV1 | None = None,
    ) -> None:
        if len(movements) >= MAX_NATIVE_MOVEMENTS:
            raise BusinessRuleError("Native physical movement oracle exceeds its limit")
        movements.append(
            NativePhysicalCashMovementV1(
                id=uuid4(),
                line_id=line_id,
                lot_id=lot_id,
                pack_id=pack_id,
                kind=kind,
                entry_order=len(movements),
                closed_delta=closed_delta,
                units_taken=units_taken,
                before_state=before,
                after_state=after,
            )
        )

    for line_id in plan.allocation_order:
        allocation, line = allocation_by_id[line_id], line_by_id[line_id]
        before = {batch.id: batch for batch in allocation.before_batches}
        after = {batch.id: batch for batch in allocation.after_batches}
        processed: set[tuple[UUID, UUID]] = set()
        for pick in allocation.picks:
            if pick.closed_packs:
                post(
                    line_id=line_id,
                    lot_id=pick.batch_id,
                    kind="pack_take",
                    units_taken=pick.base_units,
                    closed_delta=-pick.closed_packs,
                )
                continue
            if pick.opened_pack_id is None:
                raise BusinessRuleError("Native physical allocation lost its pack identity")
            key = (pick.batch_id, pick.opened_pack_id)
            if key in processed:
                continue
            processed.add(key)
            pack_before = next(
                (pack for pack in before[pick.batch_id].opened_packs if pack.id == key[1]), None
            )
            pack_after = next(
                (pack for pack in after[pick.batch_id].opened_packs if pack.id == key[1]), None
            )
            if pick.newly_opened and key[1] not in opened_aliases:
                opened_aliases.add(key[1])
                post(
                    line_id=line_id,
                    lot_id=pick.batch_id,
                    kind="pack_open",
                    units_taken=0,
                    closed_delta=-1,
                    pack_id=key[1],
                    after=full_state(line),
                )
            post(
                line_id=line_id,
                lot_id=pick.batch_id,
                kind="blister_take" if line.sale_format == "blister" else "unit_take",
                units_taken=sum(
                    other.base_units
                    for other in allocation.picks
                    if (other.batch_id, other.opened_pack_id) == key
                ),
                pack_id=key[1],
                before=state(pack_before) if pack_before else full_state(line),
                after=state(pack_after),
            )
    return tuple(movements)


def build_edge_physical_cash_plan(draft: EdgeRetailCashDraftV1) -> NativePhysicalCashPlanV1:
    """Recompute from current DB-origin facts while the caller retains its locks."""
    from app.domains.inventory.unit_dispensing_v2_contract import physical_cart_fingerprint

    draft = EdgeRetailCashDraftV1.model_validate(draft.model_dump(mode="python"))
    if draft.inputs.preparation.kind != "physical_cash" or draft.legacy_items:
        raise BusinessRuleError("Native physical execution does not support mixed checkout")
    checked = check_edge_retail_cash_draft(draft)
    physical = tuple(
        line for line in checked.quote.lines if isinstance(line, PhysicalUnitDraftQuoteLineV2)
    )
    lines = tuple(
        UnitCartLineV2(
            id=line.id,
            catalog_id=line.catalog_id,
            packaging=line.packaging,
            sale_format=line.sale_format,
            quantity=line.quantity,
        )
        for line in physical
    )
    scope = draft.inputs.preparation.scope
    plan = NativePhysicalCashPlanV1(
        quote=checked.quote,
        stock_request_hash=physical_cart_fingerprint(
            scope.tenant_id,
            scope.branch_id,
            lines,
            tuple(line.discount_amount for line in physical),
        ),
        movements=_movement_oracle(checked.physical_stock_plan, lines),
    )
    encoded = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
        raise BusinessRuleError("Native physical movement oracle exceeds the wire bound")
    return plan
