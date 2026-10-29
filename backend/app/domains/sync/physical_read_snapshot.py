"""Bounded historical physical facts for the explicitly opt-in read-only profile."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, MAX_MONEY, UnitSalePreparation
from app.domains.inventory.unit_stock_v2 import OpenedPackV2, UnitPackagingV2, UnitStockBatchV2
from app.domains.sync.physical_read_snapshot_v1 import PhysicalLegacyLotRecord

MAX_PHYSICAL_RECORDS = 10_000
MAX_PHYSICAL_BYTES = 8 * 1024 * 1024


def _exact_money(value: object) -> object:
    if isinstance(value, bool | float):
        raise ValueError("Physical cost requires an exact decimal representation")
    return value


Money = Annotated[
    Decimal,
    BeforeValidator(_exact_money),
    Field(ge=0, le=MAX_MONEY, max_digits=14, decimal_places=2, allow_inf_nan=False),
]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PhysicalCatalogConfigRecord(_Record):
    record_type: Literal["physical_catalog_config"] = "physical_catalog_config"
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    preparation: UnitSalePreparation | None


class PhysicalOpenedPackRecord(_Record):
    id: UUID
    pack_ordinal: int = Field(strict=True, ge=1)
    opened_at: AwareDatetime
    state: OpenedPackV2
    remaining_units: int = Field(strict=True, ge=0, le=MAX_BASE_UNITS)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.state.id != self.id or self.state.opened_at != self.opened_at:
            raise ValueError("Physical pack identity does not match its state")
        return self


class PhysicalCostRecord(_Record):
    status: Literal["known", "unknown"]
    version: int | None = Field(default=None, strict=True, ge=1)
    initial_cost: Money | None
    remaining_cost: Money | None

    @model_validator(mode="after")
    def validate_cost(self) -> Self:
        known = self.status == "known"
        initial, remaining = self.initial_cost, self.remaining_cost
        if known != (initial is not None and remaining is not None):
            raise ValueError("Physical cost status does not match historical evidence")
        if known and (
            self.version != 1 or initial is None or remaining is None or remaining > initial
        ):
            raise ValueError("Physical cost evidence is inconsistent")
        if not known and (self.initial_cost is not None or self.remaining_cost is not None):
            raise ValueError("Unknown physical cost must not invent monetary evidence")
        return self


class PhysicalOpeningBasisRecord(_Record):
    id: UUID
    lot_id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    transition_id: UUID
    pack_ordinal: int = Field(strict=True, ge=1)
    opened_at: AwareDatetime
    state: OpenedPackV2
    remaining_units: int = Field(strict=True, ge=1, le=MAX_BASE_UNITS)
    cost_amount: Money


class PhysicalLotRecord(_Record):
    record_type: Literal["physical_lot"] = "physical_lot"
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    source_item_id: UUID
    expires_at: date
    created_at: AwareDatetime
    packaging: UnitPackagingV2
    initial_closed_packs: int = Field(strict=True, ge=0)
    initial_opened_packs: int = Field(strict=True, ge=0)
    initial_opened_units: int = Field(strict=True, ge=0)
    initial_opened_cost: Money
    purchase_cost_per_pack: Money | None
    closed_packs: int = Field(strict=True, ge=0)
    is_blocked: bool = Field(strict=True)
    blocking_revision: int = Field(strict=True, ge=0)
    opened_packs: tuple[PhysicalOpenedPackRecord, ...]
    opening_basis: tuple[PhysicalOpeningBasisRecord, ...] = ()
    cost: PhysicalCostRecord

    @model_validator(mode="after")
    def validate_physical_history(self) -> Self:
        ordinals = [pack.pack_ordinal for pack in self.opened_packs]
        ids = [pack.id for pack in self.opened_packs]
        if len(set(ordinals)) != len(ordinals) or len(set(ids)) != len(ids):
            raise ValueError("Physical pack identities must be unique")
        if self.closed_packs > self.initial_closed_packs:
            raise ValueError("Physical closed stock exceeds its historical initialization")
        if (
            (self.initial_opened_packs == 0) != (self.initial_opened_units == 0)
            or self.initial_opened_units > self.initial_opened_packs * self.packaging.units_per_pack
            or (self.initial_opened_packs == 0 and self.initial_opened_cost != 0)
        ):
            raise ValueError("Physical opened initialization is inconsistent")
        self._validate_opening_basis()
        opened_end = self.initial_closed_packs + self.initial_opened_packs - self.closed_packs
        if any(n > opened_end for n in ordinals):
            raise ValueError("Physical pack ordinal exceeds historical initialization")
        active = tuple(p.state for p in self.opened_packs if p.remaining_units > 0)
        stock = UnitStockBatchV2(
            id=self.id,
            tenant_id=self.tenant_id,
            branch_id=self.branch_id,
            catalog_id=self.catalog_id,
            expires_at=self.expires_at,
            created_at=self.created_at,
            packaging=self.packaging,
            closed_packs=self.closed_packs,
            opened_packs=active,
            is_blocked=self.is_blocked,
        )
        from app.domains.inventory.unit_stock_v2 import _opened_units

        for pack in self.opened_packs:
            if pack.remaining_units:
                if _opened_units(pack.state, stock.packaging) != pack.remaining_units:
                    raise ValueError("Physical pack remainder differs from its state")
            elif pack.state.intact_blisters or pack.state.opened_blisters or pack.state.loose_units:
                raise ValueError("Exhausted physical pack contains remaining stock")
        return self

    def _validate_opening_basis(self) -> None:
        if self.cost.status == "known" and self.purchase_cost_per_pack is None:
            raise ValueError("Known physical cost requires its historical pack basis")
        if self.cost.status == "known" and self.purchase_cost_per_pack is not None:
            with localcontext(Context(prec=40)):
                if (
                    self.cost.initial_cost
                    != self.initial_closed_packs * self.purchase_cost_per_pack
                    + self.initial_opened_cost
                ):
                    raise ValueError("Physical initial cost differs from its historical basis")
        if len(self.opening_basis) != self.initial_opened_packs:
            raise ValueError("Physical opened initialization requires all original pack bases")
        if sorted(p.pack_ordinal for p in self.opening_basis) != list(
            range(1, self.initial_opened_packs + 1)
        ):
            raise ValueError("Initial opened pack ordinals must be complete")
        if sum(p.remaining_units for p in self.opening_basis) != self.initial_opened_units:
            raise ValueError("Initial opened quantities differ from their exact pack bases")
        with localcontext(Context(prec=40)):
            if (
                sum((p.cost_amount for p in self.opening_basis), Decimal("0.00"))
                != self.initial_opened_cost
            ):
                raise ValueError("Initial opened cost differs from its exact pack bases")
        for basis in self.opening_basis:
            if (basis.lot_id, basis.tenant_id, basis.branch_id, basis.catalog_id) != (
                self.id,
                self.tenant_id,
                self.branch_id,
                self.catalog_id,
            ):
                raise ValueError("Initial opened pack basis escapes its physical lot scope")
            current = next(
                (pack for pack in self.opened_packs if pack.pack_ordinal == basis.pack_ordinal),
                None,
            )
            if (
                current is None
                or current.id != basis.id
                or current.opened_at != basis.opened_at
                or basis.state.id != basis.id
                or basis.state.opened_at != basis.opened_at
            ):
                raise ValueError("Initial opened pack basis identity differs from current history")
            from app.domains.inventory.unit_stock_v2 import _opened_units

            if _opened_units(basis.state, self.packaging) != basis.remaining_units:
                raise ValueError("Initial opened pack basis remainder differs from its state")


PhysicalRecord = Annotated[
    PhysicalCatalogConfigRecord | PhysicalLotRecord | PhysicalLegacyLotRecord,
    Field(discriminator="record_type"),
]
PHYSICAL_RECORD_ADAPTER: TypeAdapter[PhysicalRecord] = TypeAdapter(PhysicalRecord)


def validate_physical_records(
    records: Sequence[PhysicalRecord],
    *,
    tenant_id: UUID,
    branch_id: UUID,
    catalog_ids: set[UUID],
) -> tuple[PhysicalRecord, ...]:
    from app.domains.sync.integrity import canonical_json_bytes

    if len(records) > MAX_PHYSICAL_RECORDS:
        raise ValueError("Physical snapshot record limit exceeded")
    ordered = tuple(sorted(records, key=lambda row: (row.record_type, str(row.id))))
    keys = [(row.record_type, row.id) for row in ordered]
    if len(set(keys)) != len(keys):
        raise ValueError("Physical snapshot identities must be unique")
    if any(row.tenant_id != tenant_id or row.branch_id != branch_id for row in ordered):
        raise ValueError("Physical snapshot escapes its tenant or branch")
    configs = {row.id for row in ordered if isinstance(row, PhysicalCatalogConfigRecord)}
    if configs != catalog_ids:
        raise ValueError("Physical snapshot requires exactly one configuration per catalog item")
    if any(
        isinstance(row, PhysicalLotRecord | PhysicalLegacyLotRecord)
        and row.catalog_id not in configs
        for row in ordered
    ):
        raise ValueError("Physical lot references an absent catalog configuration")
    pack_ids = [
        pack.id
        for row in ordered
        if isinstance(row, PhysicalLotRecord | PhysicalLegacyLotRecord)
        for pack in row.opened_packs
    ]
    if len(set(pack_ids)) != len(pack_ids):
        raise ValueError("Opened pack identifiers must be unique across the snapshot")
    lot_ids = [
        row.id for row in ordered if isinstance(row, PhysicalLotRecord | PhysicalLegacyLotRecord)
    ]
    if len(set(lot_ids)) != len(lot_ids):
        raise ValueError("Physical lot identifiers must not alias between stock versions")
    if (
        len(canonical_json_bytes([row.model_dump(mode="json") for row in ordered]))
        > MAX_PHYSICAL_BYTES
    ):
        raise ValueError("Physical snapshot byte limit exceeded")
    return ordered


def historical_cost(
    *,
    initial_closed_packs: int,
    purchase_cost_per_pack: Decimal,
    initial_opened_cost: Decimal,
    cost_version: int | None,
    spent: Sequence[tuple[int, int | None, Decimal | None]],
) -> PhysicalCostRecord:
    if cost_version != 1 or any(
        units > 0 and (version != 1 or amount is None) for units, version, amount in spent
    ):
        return PhysicalCostRecord(
            status="unknown", version=cost_version, initial_cost=None, remaining_cost=None
        )
    with localcontext(Context(prec=40)):
        initial = initial_closed_packs * purchase_cost_per_pack + initial_opened_cost
        remaining = initial - sum(
            (amount for units, _, amount in spent if units > 0 and amount is not None),
            Decimal("0.00"),
        )
    return PhysicalCostRecord(
        status="known", version=1, initial_cost=initial, remaining_cost=remaining
    )
