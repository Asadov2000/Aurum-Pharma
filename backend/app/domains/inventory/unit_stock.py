"""Immutable physical-stock previews; these do not authorize unit sales.

A preview does not lock the database or change stock. A real write must replan
after acquiring database locks and record the operation in an immutable ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, DispenseUnit, SaleFormat


class UnitPackaging(BaseModel):
    """Explicit, versioned physical packaging; no inferred pack coefficients."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    unit: DispenseUnit
    units_per_pack: int = Field(strict=True, ge=2, le=10_000)
    dispense_step: int = Field(strict=True, ge=1, le=10_000)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_step(self) -> Self:
        if self.units_per_pack % self.dispense_step:
            raise ValueError("dispense_step must divide units_per_pack")
        return self


class OpenedPack(BaseModel):
    """Remaining physical units in one already opened pack."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    opened_at: AwareDatetime
    remaining_units: int = Field(strict=True, gt=0)


class UnitStockBatch(BaseModel):
    """An immutable stock snapshot with a fixed packaging coefficient."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    expires_at: date
    created_at: AwareDatetime
    packaging: UnitPackaging
    closed_packs: int = Field(strict=True, ge=0)
    opened_packs: tuple[OpenedPack, ...]
    is_blocked: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_stock(self) -> Self:
        pack_ids: set[UUID] = set()
        base_units = self.closed_packs * self.packaging.units_per_pack
        for pack in self.opened_packs:
            if pack.id in pack_ids:
                raise ValueError("opened pack identifiers must be unique")
            pack_ids.add(pack.id)
            if pack.remaining_units >= self.packaging.units_per_pack:
                raise ValueError("opened pack remainder must be smaller than a pack")
            base_units += pack.remaining_units
        if base_units > MAX_BASE_UNITS:
            raise ValueError("batch stock exceeds the base unit limit")
        return self


@dataclass(frozen=True)
class OpenedPackTake:
    """Units taken from an existing pack and its resulting physical remainder."""

    pack_id: UUID
    units_taken: int
    remaining_units: int


@dataclass(frozen=True)
class UnitStockPick:
    """Aggregated physical allocation from a single batch."""

    batch_id: UUID
    closed_packs_taken: int
    opened_packs: tuple[OpenedPackTake, ...]
    new_packs_opened: int
    new_opened_remainder_units: int
    base_units: int


@dataclass(frozen=True)
class UnitStockPlan:
    """A preview, never a database lock, stock mutation, or sale authorization."""

    sale_format: SaleFormat
    quantity: int
    base_units: int
    picks: tuple[UnitStockPick, ...]
    lock_batch_ids: tuple[UUID, ...]
    lock_opened_pack_ids: tuple[UUID, ...]


class UnitStockShortage(ValueError):
    """Requested and dispensable quantities, without stock identifiers."""

    def __init__(self, *, requested_base_units: int, available_base_units: int) -> None:
        self.requested_base_units = requested_base_units
        self.available_base_units = available_base_units
        super().__init__("insufficient dispensable stock")


def _requested_base_units(quantity: int, sale_format: SaleFormat, packaging: UnitPackaging) -> int:
    if type(quantity) is not int or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    if sale_format == "pack":
        base_units = quantity * packaging.units_per_pack
    elif sale_format == "unit":
        if quantity % packaging.dispense_step:
            raise ValueError("unit quantity must be a multiple of dispense_step")
        base_units = quantity
    else:
        raise ValueError("unknown sale format")
    if base_units > MAX_BASE_UNITS:
        raise ValueError("requested quantity exceeds the base unit limit")
    return base_units


def _unit_pick(batch: UnitStockBatch, remaining: int) -> UnitStockPick:
    factor = batch.packaging.units_per_pack
    step = batch.packaging.dispense_step
    requested = remaining
    opened_takes: list[OpenedPackTake] = []
    for pack in sorted(batch.opened_packs, key=lambda pack: (pack.opened_at, pack.id)):
        taken = min(remaining, pack.remaining_units // step * step)
        if taken:
            opened_takes.append(
                OpenedPackTake(
                    pack_id=pack.id,
                    units_taken=taken,
                    remaining_units=pack.remaining_units - taken,
                )
            )
            remaining -= taken
        if remaining == 0:
            break

    closed_units_taken = min(remaining, batch.closed_packs * factor)
    new_packs_opened = (closed_units_taken + factor - 1) // factor
    new_remainder = new_packs_opened * factor - closed_units_taken
    return UnitStockPick(
        batch_id=batch.id,
        closed_packs_taken=0,
        opened_packs=tuple(opened_takes),
        new_packs_opened=new_packs_opened,
        new_opened_remainder_units=new_remainder,
        base_units=requested - remaining + closed_units_taken,
    )


def _validated_snapshot_ids(
    batches: tuple[UnitStockBatch, ...],
    *,
    tenant_id: UUID,
    branch_id: UUID,
    catalog_id: UUID,
) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
    """Validate the entire input before selection and retain its lock coverage."""
    batch_ids: set[UUID] = set()
    pack_ids: set[UUID] = set()
    for batch in batches:
        if (batch.tenant_id, batch.branch_id, batch.catalog_id) != (
            tenant_id,
            branch_id,
            catalog_id,
        ):
            raise ValueError("stock snapshot is outside the requested scope")
        if batch.id in batch_ids:
            raise ValueError("batch identifiers must be unique")
        batch_ids.add(batch.id)
        for pack in batch.opened_packs:
            if pack.id in pack_ids:
                raise ValueError("opened pack identifiers must be globally unique")
            pack_ids.add(pack.id)
    return tuple(sorted(batch_ids)), tuple(sorted(pack_ids))


def plan_unit_stock(
    *,
    batches: tuple[UnitStockBatch, ...],
    tenant_id: UUID,
    branch_id: UUID,
    catalog_id: UUID,
    today: date,
    sale_format: SaleFormat,
    quantity: int,
    packaging: UnitPackaging,
) -> UnitStockPlan:
    """Preview an all-or-nothing FEFO physical allocation for one stock scope.

    This does not lock the database, change stock, or authorize a sale. A real
    write requires replanning after database locks and an immutable ledger.
    Packaging mismatches are excluded without reinterpreting their stock. Lock
    identifiers cover the entire input snapshot; they are not a locking recipe.
    """
    base_units = _requested_base_units(quantity, sale_format, packaging)
    batch_ids, pack_ids = _validated_snapshot_ids(
        batches, tenant_id=tenant_id, branch_id=branch_id, catalog_id=catalog_id
    )
    eligible: list[UnitStockBatch] = []
    available = 0
    for batch in batches:
        if batch.is_blocked or batch.expires_at <= today or batch.packaging != packaging:
            continue
        eligible.append(batch)
        available += batch.closed_packs * packaging.units_per_pack
        if sale_format == "unit":
            available += sum(
                pack.remaining_units // packaging.dispense_step * packaging.dispense_step
                for pack in batch.opened_packs
            )
    if available < base_units:
        raise UnitStockShortage(requested_base_units=base_units, available_base_units=available)

    picks: list[UnitStockPick] = []
    remaining = base_units
    for batch in sorted(eligible, key=lambda batch: (batch.expires_at, batch.created_at, batch.id)):
        if sale_format == "unit":
            pick = _unit_pick(batch, remaining)
        else:
            packs_taken = min(remaining // packaging.units_per_pack, batch.closed_packs)
            pick = UnitStockPick(
                batch_id=batch.id,
                closed_packs_taken=packs_taken,
                opened_packs=(),
                new_packs_opened=0,
                new_opened_remainder_units=0,
                base_units=packs_taken * packaging.units_per_pack,
            )
        if pick.base_units:
            picks.append(pick)
            remaining -= pick.base_units
        if remaining == 0:
            break
    return UnitStockPlan(
        sale_format=sale_format,
        quantity=quantity,
        base_units=base_units,
        picks=tuple(picks),
        lock_batch_ids=batch_ids,
        lock_opened_pack_ids=pack_ids,
    )
