"""Explicit blister-aware physical snapshots, without posting or sale authorization.

Version 1 contracts and their immutable hashes remain unchanged. These snapshots
must come from historical stock facts, never from today's catalog configuration.
Availability is not a reservation; a future writer must lock, recheck and journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, DispenseUnit

SaleFormatV2 = Literal["pack", "blister", "unit"]
BlisterNumber = Annotated[int, Field(strict=True, ge=1)]


class UnitPackagingV2(BaseModel):
    """Historical composition; explicit null means unknown blister composition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[2] = 2
    unit: DispenseUnit
    units_per_pack: int = Field(strict=True, ge=2, le=10_000)
    units_per_blister: int | None = Field(strict=True, ge=2, le=10_000)
    dispense_step: int = Field(strict=True, ge=1, le=10_000)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_composition(self) -> Self:
        if self.units_per_pack % self.dispense_step:
            raise ValueError("dispense_step must divide units_per_pack")
        if self.units_per_blister is not None and self.units_per_pack % self.units_per_blister:
            raise ValueError("units_per_blister must divide units_per_pack")
        return self


class OpenedBlisterV2(BaseModel):
    """Physical identity is the enclosing pack UUID plus this blister number."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: BlisterNumber
    remaining_units: int = Field(strict=True, gt=0)


class OpenedPackV2(BaseModel):
    """Opened carton; it may still contain all its original, intact blisters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    opened_at: AwareDatetime
    intact_blisters: tuple[BlisterNumber, ...] = ()
    opened_blisters: tuple[OpenedBlisterV2, ...] = ()
    loose_units: int = Field(default=0, strict=True, ge=0)


def _opened_units(pack: OpenedPackV2, packaging: UnitPackagingV2) -> int:
    size = packaging.units_per_blister
    if size is None:
        if pack.intact_blisters or pack.opened_blisters:
            raise ValueError("blister identities require known historical composition")
        total = pack.loose_units
    else:
        if pack.loose_units:
            raise ValueError("known blisters require individually identified remainders")
        numbers = pack.intact_blisters + tuple(blister.number for blister in pack.opened_blisters)
        if len(set(numbers)) != len(numbers):
            raise ValueError("blister numbers must be unique within a pack")
        if any(number > packaging.units_per_pack // size for number in numbers):
            raise ValueError("blister number is outside the physical pack")
        if any(blister.remaining_units >= size for blister in pack.opened_blisters):
            raise ValueError("opened blister remainder must be smaller than an intact blister")
        total = len(pack.intact_blisters) * size + sum(
            blister.remaining_units for blister in pack.opened_blisters
        )
    if not 0 < total <= packaging.units_per_pack:
        raise ValueError("opened pack must contain a positive remainder within pack capacity")
    return total


class UnitStockBatchV2(BaseModel):
    """Physical facts for one batch, with explicit sealed-pack and blister states."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    expires_at: date
    created_at: AwareDatetime
    packaging: UnitPackagingV2
    closed_packs: int = Field(strict=True, ge=0)
    opened_packs: tuple[OpenedPackV2, ...]
    is_blocked: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_stock(self) -> Self:
        ids = tuple(pack.id for pack in self.opened_packs)
        if len(set(ids)) != len(ids):
            raise ValueError("opened pack identifiers must be unique")
        total = self.closed_packs * self.packaging.units_per_pack + sum(
            _opened_units(pack, self.packaging) for pack in self.opened_packs
        )
        if total > MAX_BASE_UNITS:
            raise ValueError("batch stock exceeds the base unit limit")
        return self


def to_base_units_v2(
    packaging: UnitPackagingV2, *, quantity: int, sale_format: SaleFormatV2
) -> int:
    """Convert explicit whole quantities; composition never implies physical availability."""
    if not isinstance(packaging, UnitPackagingV2):
        raise ValueError("version 2 packaging is required")
    if type(quantity) is not int or not 0 < quantity <= MAX_BASE_UNITS:
        raise ValueError("quantity must be a positive integer within the stock limit")
    if sale_format == "pack":
        units = quantity * packaging.units_per_pack
    elif sale_format == "blister":
        if packaging.units_per_blister is None:
            raise ValueError("blister format requires known historical composition")
        units = quantity * packaging.units_per_blister
    elif sale_format == "unit":
        units = quantity
    else:
        raise ValueError("unknown sale format")
    if units > MAX_BASE_UNITS:
        raise ValueError("base quantity exceeds the stock limit")
    if units % packaging.dispense_step:
        raise ValueError("base quantity must be a multiple of dispense_step")
    return units


@dataclass(frozen=True)
class UnitStockAvailabilityV2:
    """Physical counts, not an allocation, lock recipe or permission to dispense."""

    base_units: int
    closed_packs: int
    intact_blisters: int | None
    lock_batch_ids: tuple[UUID, ...]
    lock_opened_pack_ids: tuple[UUID, ...]


def stock_availability_v2(
    *,
    batches: tuple[UnitStockBatchV2, ...],
    tenant_id: UUID,
    branch_id: UUID,
    catalog_id: UUID,
    today: date,
    packaging: UnitPackagingV2,
) -> UnitStockAvailabilityV2:
    """Validate the entire supplied scope, then count eligible physical stock.

    Fragments never become intact blisters or sealed packs. Base units are raw
    physical units, including remnants smaller than dispense_step. Future
    allocation must enforce the step, physical state and whole-cart requirements.
    """
    if not isinstance(packaging, UnitPackagingV2):
        raise ValueError("version 2 packaging is required")
    if type(today) is not date:
        raise ValueError("today must be a calendar date")
    if not isinstance(batches, tuple) or any(
        not isinstance(batch, UnitStockBatchV2) for batch in batches
    ):
        raise ValueError("version 2 batch snapshots are required")
    batch_ids: set[UUID] = set()
    pack_ids: set[UUID] = set()
    units = closed = intact = 0
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
        if batch.is_blocked or batch.expires_at <= today or batch.packaging != packaging:
            continue
        closed += batch.closed_packs
        units += batch.closed_packs * packaging.units_per_pack + sum(
            _opened_units(pack, packaging) for pack in batch.opened_packs
        )
        if packaging.units_per_blister is not None:
            intact += batch.closed_packs * (packaging.units_per_pack // packaging.units_per_blister)
            intact += sum(len(pack.intact_blisters) for pack in batch.opened_packs)
    if units > MAX_BASE_UNITS:
        raise ValueError("eligible stock exceeds the base unit limit")
    return UnitStockAvailabilityV2(
        base_units=units,
        closed_packs=closed,
        intact_blisters=intact if packaging.units_per_blister is not None else None,
        lock_batch_ids=tuple(sorted(batch_ids)),
        lock_opened_pack_ids=tuple(sorted(pack_ids)),
    )
