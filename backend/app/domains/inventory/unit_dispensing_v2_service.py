"""Atomically journal locked physical stock; not a sale, payment or receipt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.unit_allocation_v2 import (
    UnitCartLineV2,
    UnitCartStockPlanV2,
    plan_unit_cart_stock_v2,
)
from app.domains.inventory.unit_dispensing_v2_cost import (
    UnitCartDispenseCostResultV2,
    persisted_cost_result_v2,
)
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_ledger_v2_models import (
    UnitStockCartCommandV2,
    UnitStockCartLineV2,
    UnitStockLotV2,
    UnitStockMovementV2,
    UnitStockOpenedPackV2,
)
from app.domains.inventory.unit_stock_v2 import (
    OpenedPackV2,
    UnitPackagingV2,
    UnitStockBatchV2,
    to_base_units_v2,
)


@dataclass(frozen=True)
class UnitCartDispenseLineResultV2:
    line_id: UUID
    catalog_id: UUID
    sale_format: str
    quantity: int
    base_units: int


@dataclass(frozen=True)
class UnitCartMovementResultV2:
    id: UUID
    line_id: UUID
    lot_id: UUID
    pack_id: UUID | None
    kind: str
    entry_order: int
    closed_delta: int
    units_taken: int
    sealed_start: int | None
    sealed_count: int | None
    before_state: dict[str, object] | None
    after_state: dict[str, object] | None


@dataclass(frozen=True)
class UnitCartDispenseResultV2:
    command_id: UUID
    lines: tuple[UnitCartDispenseLineResultV2, ...]
    movements: tuple[UnitCartMovementResultV2, ...]


def _fingerprint(tenant_id: UUID, branch_id: UUID, lines: tuple[UnitCartLineV2, ...]) -> str:
    payload = [
        "aurum-unit-cart-v2:1",
        str(tenant_id),
        str(branch_id),
        [
            [
                str(line.id),
                str(line.catalog_id),
                line.packaging.version,
                line.packaging.unit,
                line.packaging.units_per_pack,
                line.packaging.units_per_blister,
                line.packaging.dispense_step,
                line.sale_format,
                line.quantity,
            ]
            for line in lines
        ],
    ]
    return sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _state(pack: OpenedPackV2 | None) -> dict[str, object]:
    if pack is None:
        return {"intact_blisters": [], "opened_blisters": [], "loose_units": 0}
    return pack.model_dump(mode="json", exclude={"id", "opened_at"})


def _full_state(packaging: UnitPackagingV2) -> dict[str, object]:
    size = packaging.units_per_blister
    return {
        "intact_blisters": list(range(1, packaging.units_per_pack // size + 1)) if size else [],
        "opened_blisters": [],
        "loose_units": 0 if size else packaging.units_per_pack,
    }


def _checked_lines(
    lines: tuple[UnitCartLineV2, ...],
) -> tuple[tuple[UnitCartLineV2, ...], list[int]]:
    if not isinstance(lines, tuple) or not 0 < len(lines) <= 200:
        raise BusinessRuleError("Physical cart requires between 1 and 200 lines")
    if any(not isinstance(line, UnitCartLineV2) for line in lines):
        raise BusinessRuleError("Physical cart requires version-two lines")
    try:
        lines = tuple(UnitCartLineV2.model_validate(line.model_dump()) for line in lines)
        amounts = [
            to_base_units_v2(line.packaging, quantity=line.quantity, sale_format=line.sale_format)
            for line in lines
        ]
    except ValueError as exc:
        raise BusinessRuleError("Invalid physical cart quantity or composition") from exc
    if len({line.id for line in lines}) != len(lines):
        raise BusinessRuleError("Physical cart line identifiers must be unique")
    if sum(amounts) > MAX_BASE_UNITS:
        raise BusinessRuleError("Physical cart exceeds the stock limit")
    return lines, amounts


def _stock(
    lots: list[UnitStockLotV2], packs: list[UnitStockOpenedPackV2]
) -> tuple[UnitStockBatchV2, ...]:
    return tuple(
        UnitStockBatchV2(
            id=lot.id,
            tenant_id=lot.tenant_id,
            branch_id=lot.branch_id,
            catalog_id=lot.catalog_id,
            expires_at=lot.expires_at,
            created_at=lot.created_at,
            packaging=UnitPackagingV2.model_validate(lot.packaging),
            closed_packs=lot.closed_packs,
            opened_packs=tuple(
                OpenedPackV2.model_validate(
                    {**pack.state, "id": pack.id, "opened_at": pack.opened_at}
                )
                for pack in packs
                if pack.lot_id == lot.id
            ),
        )
        for lot in lots
    )


class PhysicalUnitLedgerV2Service:
    def __init__(self, repo: UnitDispensingV2Repository) -> None:
        self.repo = repo

    async def cost_result(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        command_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> UnitCartDispenseCostResultV2:
        """Internal financial source; do not expose through physical or receipt APIs."""
        if not isinstance(allowed_branch_ids, set) or branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")
        if any(
            not isinstance(value, UUID) for value in (tenant_id, branch_id, command_id, actor_id)
        ):
            raise PermissionDeniedError("Authenticated physical cart identifiers are required")
        await self.repo.assert_received_actor(tenant_id, branch_id, actor_id)
        command = await self.repo.cart_command(tenant_id, command_id)
        if command is None:
            raise NotFoundError("Physical cart not found")
        if command.branch_id != branch_id:
            raise PermissionDeniedError("Branch access denied")
        return persisted_cost_result_v2(
            command_id, await self.repo.movements(tenant_id, command_id)
        )

    async def _result(self, tenant_id: UUID, command_id: UUID) -> UnitCartDispenseResultV2:
        lines = await self.repo.cart_lines(tenant_id, command_id)
        movements = await self.repo.movements(tenant_id, command_id)
        if not lines or not movements:
            raise ConflictError("Physical cart has no complete persisted result")
        return UnitCartDispenseResultV2(
            command_id,
            tuple(
                UnitCartDispenseLineResultV2(
                    line.line_id, line.catalog_id, line.sale_format, line.quantity, line.base_units
                )
                for line in lines
            ),
            tuple(
                UnitCartMovementResultV2(
                    movement.id,
                    movement.line_id,
                    movement.lot_id,
                    movement.pack_id,
                    movement.kind,
                    movement.entry_order,
                    movement.closed_delta,
                    movement.units_taken,
                    movement.sealed_start,
                    movement.sealed_count,
                    movement.before_state,
                    movement.after_state,
                )
                for movement in movements
            ),
        )

    async def dispense_cart(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        command_id: UUID,
        lines: tuple[UnitCartLineV2, ...],
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> UnitCartDispenseResultV2:
        if not isinstance(allowed_branch_ids, set) or branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")
        if any(
            not isinstance(value, UUID) for value in (tenant_id, branch_id, command_id, actor_id)
        ):
            raise PermissionDeniedError("Authenticated physical cart identifiers are required")
        lines, amounts = _checked_lines(lines)
        async with self.repo.session.begin_nested():
            await self.repo.assert_received_actor(tenant_id, branch_id, actor_id)
            command, created = await self.repo.claim_cart(
                UnitStockCartCommandV2(
                    tenant_id=tenant_id,
                    id=command_id,
                    branch_id=branch_id,
                    actor_id=actor_id,
                    request_hash=_fingerprint(tenant_id, branch_id, lines),
                    created_by=actor_id,
                )
            )
            if command.branch_id != branch_id or command.request_hash != _fingerprint(
                tenant_id, branch_id, lines
            ):
                raise ConflictError("Physical cart command was reused with different parameters")
            if not created:
                return await self._result(tenant_id, command_id)
            catalog_ids = sorted({line.catalog_id for line in lines})
            for catalog_id in catalog_ids:
                catalog = await self.repo.lock_catalog(tenant_id, catalog_id)
                if catalog is None or not catalog.is_active or catalog.deleted_at is not None:
                    raise BusinessRuleError("Physical cart item is unavailable")
            lots = await self.repo.lock_physical_lots(tenant_id, branch_id, catalog_ids)
            packs = await self.repo.lock_physical_packs(tenant_id, [lot.id for lot in lots])
            settings = await FoundationRepository(self.repo.session).get_settings(tenant_id)
            timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
            try:
                today = utc_now().astimezone(ZoneInfo(timezone_name)).date()
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise AurumError("Tenant report timezone is invalid") from exc
            try:
                plan = plan_unit_cart_stock_v2(
                    lines=lines,
                    batches=_stock(lots, packs),
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    today=today,
                )
            except ValueError as exc:
                raise BusinessRuleError("Insufficient compatible physical stock for cart") from exc
            await self.repo.insert_cart_lines(
                [
                    UnitStockCartLineV2(
                        tenant_id=tenant_id,
                        command_id=command_id,
                        line_id=line.id,
                        position=position,
                        catalog_id=line.catalog_id,
                        packaging=line.packaging.model_dump(mode="json"),
                        sale_format=line.sale_format,
                        quantity=line.quantity,
                        base_units=amounts[position],
                        created_by=actor_id,
                    )
                    for position, line in enumerate(lines)
                ]
            )
            await self._post(plan, lots, lines, command)
            return await self._result(tenant_id, command_id)

    async def _post(
        self,
        plan: UnitCartStockPlanV2,
        lots: list[UnitStockLotV2],
        lines: tuple[UnitCartLineV2, ...],
        command: UnitStockCartCommandV2,
    ) -> None:
        lot_by_id = {lot.id: lot for lot in lots}
        line_by_id = {line.id: line for line in lines}
        allocation_by_id = {allocation.line_id: allocation for allocation in plan.lines}
        closed = {lot.id: lot.closed_packs for lot in lots}
        real_packs: dict[UUID, UUID] = {}
        entry_order = 0

        async def post(
            *,
            lot_id: UUID,
            line_id: UUID,
            kind: str,
            units_taken: int,
            closed_delta: int = 0,
            pack_id: UUID | None = None,
            before_state: dict[str, object] | None = None,
            after_state: dict[str, object] | None = None,
        ) -> None:
            nonlocal entry_order
            lot = lot_by_id[lot_id]
            sealed_count = -closed_delta if closed_delta else None
            sealed_start = lot.initial_closed_packs - closed[lot_id] + 1 if sealed_count else None
            await self.repo.insert_movement(
                UnitStockMovementV2(
                    id=uuid4(),
                    tenant_id=command.tenant_id,
                    branch_id=command.branch_id,
                    catalog_id=lot.catalog_id,
                    command_id=command.id,
                    line_id=line_id,
                    lot_id=lot_id,
                    pack_id=pack_id,
                    kind=kind,
                    entry_order=entry_order,
                    closed_delta=closed_delta,
                    units_taken=units_taken,
                    sealed_start=sealed_start,
                    sealed_count=sealed_count,
                    before_state=before_state,
                    after_state=after_state,
                    created_by=command.actor_id,
                )
            )
            closed[lot_id] += closed_delta
            entry_order += 1

        for line_id in plan.allocation_order:
            allocation = allocation_by_id[line_id]
            line = line_by_id[line_id]
            before = {batch.id: batch for batch in allocation.before_batches}
            after = {batch.id: batch for batch in allocation.after_batches}
            processed: set[tuple[UUID, UUID]] = set()
            for pick in allocation.picks:
                if pick.closed_packs:
                    await post(
                        lot_id=pick.batch_id,
                        line_id=line_id,
                        kind="pack_take",
                        units_taken=pick.base_units,
                        closed_delta=-pick.closed_packs,
                    )
                    continue
                if pick.opened_pack_id is None:
                    raise AurumError("Physical allocation lost its pack identity")
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
                if pick.newly_opened and key[1] not in real_packs:
                    lot = lot_by_id[pick.batch_id]
                    ordinal = lot.initial_closed_packs - closed[lot.id] + 1
                    real_packs[key[1]] = uuid5(
                        NAMESPACE_URL,
                        f"aurum-unit-open-v2:{command.tenant_id}:{command.id}:{lot.id}:{ordinal}",
                    )
                    await post(
                        lot_id=lot.id,
                        line_id=line_id,
                        kind="pack_open",
                        units_taken=0,
                        closed_delta=-1,
                        pack_id=real_packs[key[1]],
                        after_state=_full_state(line.packaging),
                    )
                units = sum(
                    other.base_units
                    for other in allocation.picks
                    if (other.batch_id, other.opened_pack_id) == key
                )
                await post(
                    lot_id=pick.batch_id,
                    line_id=line_id,
                    kind="blister_take" if line.sale_format == "blister" else "unit_take",
                    units_taken=units,
                    pack_id=real_packs.get(key[1], key[1]),
                    before_state=(
                        _state(pack_before) if pack_before else _full_state(line.packaging)
                    ),
                    after_state=_state(pack_after),
                )
