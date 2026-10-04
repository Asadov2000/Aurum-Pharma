"""Transactional prepared stock core, deliberately absent from POS and HTTP.

Commands claim their immutable identity before catalog/lot/pack locks. A nested
transaction cleans up a failed claim; the caller still owns the outer commit.
Existing batch quantities and completed sales are never changed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.time import utc_now
from app.domains.catalog.unit_sales import MAX_BASE_UNITS, SaleFormat, UnitSalePreparation
from app.domains.inventory.unit_ledger_models import (
    UnitStockEntry,
    UnitStockLot,
    UnitStockOpenedPack,
    UnitStockOperation,
)
from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_stock import (
    OpenedPack,
    OpenedPackTake,
    UnitPackaging,
    UnitStockBatch,
    UnitStockPick,
    plan_unit_stock,
)
from app.domains.pos.unit_money import allocate_unit_amount


@dataclass(frozen=True)
class PreparedLotResult:
    operation_id: UUID
    lot_id: UUID
    base_units: int


@dataclass(frozen=True)
class UnitStockCostSlice:
    """Immutable cost of a contiguous physical pack range, in TJS."""

    entry_id: UUID
    lot_id: UUID
    pack_ordinal_start: int
    pack_count: int
    unit_offset: int
    base_units: int
    original_pack_cost: Decimal
    amount: Decimal
    version: Literal[1] = 1
    currency: Literal["TJS"] = "TJS"


@dataclass(frozen=True)
class CommittedUnitStockResult:
    """Recorded within the caller's transaction; durable only after its commit."""

    operation_id: UUID
    sale_format: SaleFormat
    quantity: int
    base_units: int
    picks: tuple[UnitStockPick, ...]
    cost_slices: tuple[UnitStockCostSlice, ...] = ()


def _base_units(packaging: UnitPackaging, quantity: int, sale_format: SaleFormat) -> int:
    if type(quantity) is not int or quantity <= 0 or quantity > MAX_BASE_UNITS:
        raise BusinessRuleError("Quantity must be a positive whole number")
    if sale_format == "pack":
        units = quantity * packaging.units_per_pack
    elif sale_format == "unit" and quantity % packaging.dispense_step == 0:
        units = quantity
    else:
        raise BusinessRuleError("Invalid sale format or dispense step")
    if units > MAX_BASE_UNITS:
        raise BusinessRuleError("Quantity exceeds the stock limit")
    return units


def _assert_branch(branch_id: UUID, allowed_branch_ids: set[UUID]) -> None:
    if not isinstance(allowed_branch_ids, set) or branch_id not in allowed_branch_ids:
        raise PermissionDeniedError("Branch access denied")


def _fingerprint(
    *,
    tenant_id: UUID,
    branch_id: UUID,
    catalog_id: UUID,
    packaging: UnitPackaging,
    kind: Literal["initialize", "dispense"],
    sale_format: SaleFormat,
    quantity: int,
    expires_at: date | None = None,
    purchase_cost_per_pack: Decimal | None = None,
) -> str:
    # Protocol v1 is explicit. Server-derived `today` is intentionally absent:
    # tomorrow's retry must return yesterday's immutable result, not dispense again.
    payload = {
        "version": 1,
        "tenant_id": str(tenant_id),
        "branch_id": str(branch_id),
        "catalog_id": str(catalog_id),
        "packaging": packaging.model_dump(mode="json"),
        "kind": kind,
        "sale_format": sale_format,
        "quantity": quantity,
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
    }
    if purchase_cost_per_pack is not None:
        canonical = allocate_unit_amount(
            purchase_cost_per_pack, total_units=1, offset=0, quantity=1
        ).total_amount
        payload["version"] = 2
        payload["purchase_cost_per_pack"] = format(canonical, ".2f")
        payload["cost_version"] = 1
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PreparedUnitLedgerService:
    """Core for new prepared lots, never authorization for a live sale."""

    def __init__(self, repo: UnitLedgerRepository) -> None:
        self.repo = repo

    async def _claim(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
        operation_id: UUID,
        packaging: UnitPackaging,
        kind: Literal["initialize", "dispense"],
        sale_format: SaleFormat,
        quantity: int,
        expires_at: date | None = None,
        purchase_cost_per_pack: Decimal | None = None,
    ) -> tuple[UnitStockOperation, bool]:
        request_hash = _fingerprint(
            tenant_id=tenant_id,
            branch_id=branch_id,
            catalog_id=catalog_id,
            packaging=packaging,
            kind=kind,
            sale_format=sale_format,
            quantity=quantity,
            expires_at=expires_at,
            purchase_cost_per_pack=purchase_cost_per_pack,
        )
        operation, created = await self.repo.claim_operation(
            UnitStockOperation(
                id=operation_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                catalog_id=catalog_id,
                kind=kind,
                sale_format=sale_format,
                quantity=quantity,
                base_units=_base_units(packaging, quantity, sale_format),
                unit=packaging.unit,
                units_per_pack=packaging.units_per_pack,
                dispense_step=packaging.dispense_step,
                request_hash=request_hash,
            )
        )
        if operation.request_hash != request_hash:
            raise ConflictError("Operation was already used for a different request")
        return operation, created

    async def prepare_lot(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
        operation_id: UUID,
        packaging: UnitPackaging,
        closed_packs: int,
        expires_at: date,
        allowed_branch_ids: set[UUID],
        purchase_cost_per_pack: Decimal | None = None,
    ) -> PreparedLotResult:
        """Create a new prepared lot; never copy or convert a legacy batch."""
        _assert_branch(branch_id, allowed_branch_ids)
        async with self.repo.session.begin_nested():
            operation, created = await self._claim(
                tenant_id=tenant_id,
                branch_id=branch_id,
                catalog_id=catalog_id,
                operation_id=operation_id,
                packaging=packaging,
                kind="initialize",
                sale_format="pack",
                quantity=closed_packs,
                expires_at=expires_at,
                purchase_cost_per_pack=purchase_cost_per_pack,
            )
            if not created:
                entries = await self.repo.get_entries(tenant_id, operation_id)
                if len(entries) != 1 or entries[0].kind != "initialize":
                    raise BusinessRuleError("Prepared operation has no valid initialization")
                return PreparedLotResult(operation_id, entries[0].lot_id, operation.base_units)
            catalog = await self.repo.lock_catalog(tenant_id, catalog_id)
            if catalog is None:
                raise NotFoundError("Catalog item not found")
            if catalog.unit_sale_preparation is None:
                raise BusinessRuleError("Catalog item has no unit sale preparation")
            preparation = UnitSalePreparation.model_validate(catalog.unit_sale_preparation)
            if (preparation.unit, preparation.units_per_pack, preparation.dispense_step) != (
                packaging.unit,
                packaging.units_per_pack,
                packaging.dispense_step,
            ):
                raise BusinessRuleError("Packaging does not match catalog preparation")
            lot_id = uuid5(NAMESPACE_URL, f"aurum-unit-lot:{tenant_id}:{operation_id}")
            await self.repo.insert_lot(
                UnitStockLot(
                    id=lot_id,
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    catalog_id=catalog_id,
                    state="prepared",
                    version=packaging.version,
                    unit=packaging.unit,
                    units_per_pack=packaging.units_per_pack,
                    dispense_step=packaging.dispense_step,
                    expires_at=expires_at,
                    is_blocked=False,
                    closed_packs=0,
                    initial_closed_packs=(
                        closed_packs if purchase_cost_per_pack is not None else None
                    ),
                    purchase_cost_per_pack=purchase_cost_per_pack,
                    cost_version=1 if purchase_cost_per_pack is not None else None,
                )
            )
            await self.repo.insert_entry(
                UnitStockEntry(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    catalog_id=catalog_id,
                    operation_id=operation_id,
                    lot_id=lot_id,
                    pack_id=None,
                    kind="initialize",
                    entry_order=0,
                    closed_delta=closed_packs,
                    opened_delta=0,
                    units_delta=operation.base_units,
                    pack_remaining_after=0,
                )
            )
            return PreparedLotResult(operation_id, lot_id, operation.base_units)

    async def dispense(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
        operation_id: UUID,
        packaging: UnitPackaging,
        sale_format: SaleFormat,
        quantity: int,
        today: date,
        allowed_branch_ids: set[UUID],
        require_new_operation: bool = False,
    ) -> CommittedUnitStockResult:
        """Record only prepared stock; live POS/Edge must not call this core yet."""
        _assert_branch(branch_id, allowed_branch_ids)
        async with self.repo.session.begin_nested():
            operation, created = await self._claim(
                tenant_id=tenant_id,
                branch_id=branch_id,
                catalog_id=catalog_id,
                operation_id=operation_id,
                packaging=packaging,
                kind="dispense",
                sale_format=sale_format,
                quantity=quantity,
            )
            if require_new_operation and not created:
                raise ConflictError("Stock operation was already recorded")
            if created:
                await self._record_dispense(operation, packaging, today)
            return await self._result(operation)

    async def _record_dispense(
        self,
        operation: UnitStockOperation,
        packaging: UnitPackaging,
        today: date,
    ) -> None:
        if await self.repo.lock_catalog(operation.tenant_id, operation.catalog_id) is None:
            raise NotFoundError("Catalog item not found")
        lots = await self.repo.lock_lots(
            operation.tenant_id,
            operation.branch_id,
            operation.catalog_id,
        )
        packs = await self.repo.lock_packs(operation.tenant_id, [lot.id for lot in lots])
        opened_by_lot: dict[UUID, list[OpenedPack]] = {}
        for pack in packs:
            opened_by_lot.setdefault(pack.lot_id, []).append(
                OpenedPack(
                    id=pack.id,
                    opened_at=pack.opened_at,
                    remaining_units=pack.remaining_units,
                )
            )
        snapshots = tuple(
            UnitStockBatch(
                id=lot.id,
                tenant_id=lot.tenant_id,
                branch_id=lot.branch_id,
                catalog_id=lot.catalog_id,
                expires_at=lot.expires_at,
                created_at=lot.created_at,
                packaging=UnitPackaging(
                    version=lot.version,
                    unit=lot.unit,
                    units_per_pack=lot.units_per_pack,
                    dispense_step=lot.dispense_step,
                ),
                closed_packs=lot.closed_packs,
                is_blocked=lot.is_blocked,
                opened_packs=tuple(opened_by_lot.get(lot.id, ())),
            )
            for lot in lots
        )
        plan = plan_unit_stock(
            batches=snapshots,
            tenant_id=operation.tenant_id,
            branch_id=operation.branch_id,
            catalog_id=operation.catalog_id,
            today=today,
            sale_format=operation.sale_format,
            quantity=operation.quantity,
            packaging=packaging,
        )
        lots_by_id = {lot.id: lot for lot in lots}
        order = 0
        for pick in plan.picks:
            lot = lots_by_id[pick.batch_id]
            initial_closed_packs = lot.initial_closed_packs
            for take in pick.opened_packs:
                await self._entry(
                    operation,
                    pick.batch_id,
                    order,
                    "unit_take",
                    pack_id=take.pack_id,
                    opened_delta=-take.units_taken,
                    units_delta=-take.units_taken,
                    remaining=take.remaining_units,
                )
                order += 1
            if pick.closed_packs_taken:
                await self._entry(
                    operation,
                    pick.batch_id,
                    order,
                    "pack_take",
                    closed_delta=-pick.closed_packs_taken,
                    units_delta=-pick.closed_packs_taken * packaging.units_per_pack,
                )
                order += 1
            if pick.new_packs_opened:
                pack_id: UUID | None = None
                if pick.new_opened_remainder_units:
                    pack_id = uuid5(
                        NAMESPACE_URL,
                        f"aurum-unit-remainder:{operation.tenant_id}:"
                        f"{operation.id}:{pick.batch_id}",
                    )
                    await self.repo.insert_pack(
                        UnitStockOpenedPack(
                            id=pack_id,
                            tenant_id=operation.tenant_id,
                            branch_id=operation.branch_id,
                            catalog_id=operation.catalog_id,
                            lot_id=pick.batch_id,
                            pack_ordinal=(
                                initial_closed_packs
                                - lot.closed_packs
                                + pick.closed_packs_taken
                                + pick.new_packs_opened
                                if initial_closed_packs is not None
                                else None
                            ),
                            opened_at=utc_now(),
                            remaining_units=0,
                        )
                    )
                units = (
                    pick.new_packs_opened * packaging.units_per_pack
                    - pick.new_opened_remainder_units
                )
                await self._entry(
                    operation,
                    pick.batch_id,
                    order,
                    "unit_open",
                    pack_id=pack_id,
                    closed_delta=-pick.new_packs_opened,
                    opened_delta=pick.new_opened_remainder_units,
                    units_delta=-units,
                    remaining=pick.new_opened_remainder_units,
                )
                order += 1

    async def _entry(
        self,
        operation: UnitStockOperation,
        lot_id: UUID,
        order: int,
        kind: Literal["pack_take", "unit_take", "unit_open"],
        *,
        pack_id: UUID | None = None,
        closed_delta: int = 0,
        opened_delta: int = 0,
        units_delta: int,
        remaining: int = 0,
    ) -> None:
        await self.repo.insert_entry(
            UnitStockEntry(
                id=uuid4(),
                tenant_id=operation.tenant_id,
                branch_id=operation.branch_id,
                catalog_id=operation.catalog_id,
                operation_id=operation.id,
                lot_id=lot_id,
                pack_id=pack_id,
                kind=kind,
                entry_order=order,
                closed_delta=closed_delta,
                opened_delta=opened_delta,
                units_delta=units_delta,
                pack_remaining_after=remaining,
            )
        )

    async def _result(self, operation: UnitStockOperation) -> CommittedUnitStockResult:
        entries = await self.repo.get_entries(operation.tenant_id, operation.id)
        cost_slices: list[UnitStockCostSlice] = []
        grouped: dict[UUID, list[UnitStockEntry]] = {}
        for entry in entries:
            grouped.setdefault(entry.lot_id, []).append(entry)
            if entry.cost_version is not None:
                if (
                    entry.cost_version != 1
                    or entry.cost_pack_ordinal_start is None
                    or entry.cost_pack_count is None
                    or entry.cost_unit_offset is None
                    or entry.cost_base_units is None
                    or entry.cost_original_pack_cost is None
                    or entry.cost_amount is None
                ):
                    raise BusinessRuleError("Prepared operation has an invalid cost snapshot")
                cost_slices.append(
                    UnitStockCostSlice(
                        entry.id,
                        entry.lot_id,
                        entry.cost_pack_ordinal_start,
                        entry.cost_pack_count,
                        entry.cost_unit_offset,
                        entry.cost_base_units,
                        entry.cost_original_pack_cost,
                        entry.cost_amount,
                    )
                )
        picks: list[UnitStockPick] = []
        for lot_id, movements in grouped.items():
            opened: list[OpenedPackTake] = []
            closed = new_packs = remainder = units = 0
            for entry in movements:
                units -= entry.units_delta
                if entry.kind == "unit_take" and entry.pack_id is not None:
                    opened.append(
                        OpenedPackTake(
                            entry.pack_id, -entry.opened_delta, entry.pack_remaining_after
                        )
                    )
                elif entry.kind == "pack_take":
                    closed += -entry.closed_delta
                elif entry.kind == "unit_open":
                    new_packs += -entry.closed_delta
                    remainder += entry.opened_delta
            picks.append(UnitStockPick(lot_id, closed, tuple(opened), new_packs, remainder, units))
        if not entries or sum(pick.base_units for pick in picks) != operation.base_units:
            raise BusinessRuleError("Prepared operation has no complete stock result")
        return CommittedUnitStockResult(
            operation.id,
            operation.sale_format,
            operation.quantity,
            operation.base_units,
            tuple(picks),
            tuple(cost_slices),
        )
