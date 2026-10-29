"""Historical unit-only stock, kept distinct from physical blister stock."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, MAX_MONEY
from app.domains.inventory.unit_ledger_models import (
    UnitStockEntry,
    UnitStockLot,
    UnitStockOpenedPack,
)
from app.domains.inventory.unit_stock import OpenedPack, UnitPackaging, UnitStockBatch

Money = Annotated[
    Decimal,
    Field(ge=0, le=MAX_MONEY, max_digits=14, decimal_places=2, allow_inf_nan=False),
]
Count = Annotated[int, Field(strict=True, ge=0, le=MAX_BASE_UNITS)]
Ordinal = Annotated[int, Field(strict=True, ge=1, le=MAX_BASE_UNITS)]


class PhysicalLegacyOpenedPackRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    pack_ordinal: Ordinal | None
    opened_at: AwareDatetime
    remaining_units: Count


class PhysicalLegacyLotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_type: Literal["physical_legacy_lot"] = "physical_legacy_lot"
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    state: Literal["prepared", "received"]
    source_item_id: UUID | None
    expires_at: date
    created_at: AwareDatetime
    packaging: UnitPackaging
    initial_closed_packs: Count | None
    purchase_cost_per_pack: Money | None
    cost_version: Literal[1] | None
    closed_packs: Count
    is_blocked: bool = Field(strict=True)
    opened_packs: tuple[PhysicalLegacyOpenedPackRecord, ...]
    cost_status: Literal["known", "unknown"]
    initial_cost: Money | None
    remaining_cost: Money | None

    @field_validator("cost_version", mode="before")
    @classmethod
    def strict_cost_version(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            raise ValueError("Historical cost version must be an integer")
        return value

    @field_validator("purchase_cost_per_pack", "initial_cost", "remaining_cost", mode="before")
    @classmethod
    def exact_money(cls, value: object) -> object:
        if isinstance(value, bool | float):
            raise ValueError("Historical money must use an exact decimal representation")
        return value

    def stock_snapshot(self) -> UnitStockBatch:
        """Reuse the existing unit-only availability rules without inventing blisters."""
        return UnitStockBatch(
            id=self.id,
            tenant_id=self.tenant_id,
            branch_id=self.branch_id,
            catalog_id=self.catalog_id,
            expires_at=self.expires_at,
            created_at=self.created_at,
            packaging=self.packaging,
            closed_packs=self.closed_packs,
            opened_packs=tuple(
                OpenedPack(
                    id=pack.id, opened_at=pack.opened_at, remaining_units=pack.remaining_units
                )
                for pack in self.opened_packs
                if pack.remaining_units > 0
            ),
            is_blocked=self.is_blocked,
        )

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        ids = [pack.id for pack in self.opened_packs]
        ordinals = [
            pack.pack_ordinal for pack in self.opened_packs if pack.pack_ordinal is not None
        ]
        if len(set(ids)) != len(ids) or len(set(ordinals)) != len(ordinals):
            raise ValueError("Legacy physical pack identities must be unique")
        if self.initial_closed_packs is not None:
            opened = self.initial_closed_packs - self.closed_packs
            if opened < 0 or any(ordinal > opened for ordinal in ordinals):
                raise ValueError("Legacy physical ordinal exceeds opened historical stock")
        if self.state == "received" and self.source_item_id is None:
            raise ValueError("Received physical stock requires its historical source")
        if self.cost_version is not None and (
            self.initial_closed_packs is None
            or self.initial_closed_packs == 0
            or self.purchase_cost_per_pack is None
            or any(pack.pack_ordinal is None for pack in self.opened_packs)
        ):
            raise ValueError("Versioned legacy cost requires historical initialization")
        if self.cost_status == "known":
            initial_packs = self.initial_closed_packs
            pack_cost = self.purchase_cost_per_pack
            if (
                self.cost_version != 1
                or initial_packs is None
                or pack_cost is None
                or self.initial_cost is None
                or self.remaining_cost is None
                or self.remaining_cost > self.initial_cost
            ):
                raise ValueError("Known legacy cost requires complete historical evidence")
            with localcontext(Context(prec=40)):
                initial = initial_packs * pack_cost
            if self.initial_cost != initial:
                raise ValueError("Legacy initial cost differs from its saved basis")
        elif self.initial_cost is not None or self.remaining_cost is not None:
            raise ValueError("Unknown legacy cost must not invent monetary values")
        self.stock_snapshot()
        return self


def build_legacy_physical_lot(
    lot: UnitStockLot,
    opened_packs: Sequence[UnitStockOpenedPack],
    entries: Sequence[UnitStockEntry],
    *,
    source_item_id: UUID | None = None,
) -> PhysicalLegacyLotRecord:
    """Build from one coherent source read; all rows must belong to this lot."""
    if len({entry.id for entry in entries}) != len(entries):
        raise ValueError("Legacy source entries must be unique")
    source_rows: Sequence[UnitStockOpenedPack | UnitStockEntry] = (*opened_packs, *entries)
    for row in source_rows:
        if (row.tenant_id, row.branch_id, row.catalog_id, row.lot_id) != (
            lot.tenant_id,
            lot.branch_id,
            lot.catalog_id,
            lot.id,
        ):
            raise ValueError("Legacy source row escapes its lot scope")
    complete = (
        lot.cost_version == 1
        and lot.initial_closed_packs is not None
        and lot.purchase_cost_per_pack is not None
        and all(
            entry.cost_version == 1 and entry.cost_amount is not None
            for entry in entries
            if entry.units_delta < 0
        )
    )
    initial: Decimal | None = None
    remaining: Decimal | None = None
    if complete:
        assert lot.initial_closed_packs is not None and lot.purchase_cost_per_pack is not None
        with localcontext(Context(prec=40)):
            initial = lot.initial_closed_packs * lot.purchase_cost_per_pack
            remaining = initial - sum(
                (
                    entry.cost_amount
                    for entry in entries
                    if entry.units_delta < 0 and entry.cost_amount is not None
                ),
                Decimal("0.00"),
            )
    return PhysicalLegacyLotRecord.model_validate(
        {
            "id": lot.id,
            "tenant_id": lot.tenant_id,
            "branch_id": lot.branch_id,
            "catalog_id": lot.catalog_id,
            "state": lot.state,
            "source_item_id": source_item_id,
            "expires_at": lot.expires_at,
            "created_at": lot.created_at,
            "packaging": {
                "version": lot.version,
                "unit": lot.unit,
                "units_per_pack": lot.units_per_pack,
                "dispense_step": lot.dispense_step,
            },
            "initial_closed_packs": lot.initial_closed_packs,
            "purchase_cost_per_pack": lot.purchase_cost_per_pack,
            "cost_version": lot.cost_version,
            "closed_packs": lot.closed_packs,
            "is_blocked": lot.is_blocked,
            "opened_packs": [
                {
                    "id": pack.id,
                    "pack_ordinal": pack.pack_ordinal,
                    "opened_at": pack.opened_at,
                    "remaining_units": pack.remaining_units,
                }
                for pack in sorted(opened_packs, key=lambda pack: str(pack.id))
            ],
            "cost_status": "known" if complete else "unknown",
            "initial_cost": initial,
            "remaining_cost": remaining,
        }
    )
