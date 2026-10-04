"""Internal customer returns reserve physical units in immutable quarantine.

No payment or sale is created, and no stock is credited by this service.
"""

from __future__ import annotations

import json
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Annotated, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, field_validator, model_validator

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.catalog.unit_sales import MAX_BASE_UNITS, SaleFormat
from app.domains.inventory.unit_ledger_models import UnitStockEntry
from app.domains.inventory.unit_stock import UnitPackaging
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    FrozenContract,
    Money,
    Position,
    WholeQuantity,
)
from app.domains.pos.unit_money import allocate_unit_amount
from app.domains.pos.unit_return_models import UnitReturnCommand, UnitReturnSlice
from app.domains.pos.unit_return_repository import UnitReturnRepository

Offset = Annotated[int, Field(strict=True, ge=0, le=MAX_BASE_UNITS)]


class UnitReturnSliceResult(FrozenContract):
    id: UUID
    position: Position
    original_entry_id: UUID
    lot_id: UUID
    pack_ordinal_start: WholeQuantity
    pack_count: WholeQuantity
    unit_offset: Offset
    base_units: WholeQuantity
    original_pack_cost: Money
    cost_amount: Money
    money_version: Literal[1]
    currency: Literal["TJS"]


class UnitReturnResult(FrozenContract):
    command_id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    actor_id: UUID
    original_line_id: UUID
    state: Literal["prepared_quarantine"]
    packaging: UnitPackaging
    sale_format: SaleFormat
    quantity: WholeQuantity
    allocation_offset: Offset
    base_units: WholeQuantity
    original_retail_unit_price: Money
    gross_amount: Money
    discount_amount: Money
    net_amount: Money
    cost_amount: Money
    money_version: Literal[1]
    currency: Literal["TJS"]
    slices: tuple[UnitReturnSliceResult, ...]

    @field_validator("packaging", mode="before")
    @classmethod
    def validate_packaging(cls, value: object) -> object:
        if isinstance(value, UnitPackaging):
            return UnitPackaging.model_validate(value.model_dump())
        return value

    @model_validator(mode="after")
    def valid_totals(self) -> Self:
        factor = self.packaging.units_per_pack if self.sale_format == "pack" else 1
        if self.base_units != self.quantity * factor:
            raise ValueError("Return display quantity and base units differ")
        if self.sale_format == "unit" and (
            self.quantity % self.packaging.dispense_step
            or self.allocation_offset % self.packaging.dispense_step
        ):
            raise ValueError("Return quantity does not respect original dispense step")
        if not self.slices or sum(row.base_units for row in self.slices) != self.base_units:
            raise ValueError("Return physical slices are incomplete")
        if tuple(row.position for row in self.slices) != tuple(range(1, len(self.slices) + 1)):
            raise ValueError("Return slice positions are not contiguous")
        if len({row.id for row in self.slices}) != len(self.slices) or len(
            {row.original_entry_id for row in self.slices}
        ) != len(self.slices):
            raise ValueError("Duplicate return slice")
        with localcontext(EXACT_CONTEXT):
            if self.gross_amount != self.net_amount + self.discount_amount:
                raise ValueError("Return monetary totals differ")
            if sum((row.cost_amount for row in self.slices), Decimal("0.00")) != self.cost_amount:
                raise ValueError("Return cost slices differ")
        for row in self.slices:
            if (
                row.unit_offset >= self.packaging.units_per_pack
                or row.pack_count
                != (row.unit_offset + row.base_units + self.packaging.units_per_pack - 1)
                // self.packaging.units_per_pack
            ):
                raise ValueError("Invalid return physical pack range")
            if row.cost_amount != _physical_cost(
                row.original_pack_cost,
                self.packaging.units_per_pack,
                row.unit_offset,
                row.base_units,
            ):
                raise ValueError("Return physical cost differs")
        return self


def return_fingerprint(
    tenant_id: UUID, branch_id: UUID, original_line_id: UUID, quantity: int
) -> str:
    """Versioned request identity; the server-derived allocation cursor is excluded."""
    if type(quantity) is not int or not 0 < quantity <= MAX_BASE_UNITS:
        raise ValueError("Return quantity must be a positive whole number")
    payload = {
        "version": 1,
        "kind": "unit_return_quarantine",
        "tenant_id": str(tenant_id),
        "branch_id": str(branch_id),
        "original_line_id": str(original_line_id),
        "quantity": quantity,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _physical_cost(amount: Decimal, factor: int, offset: int, units: int) -> Decimal:
    canonical = allocate_unit_amount(amount, total_units=1, offset=0, quantity=1).amount
    with localcontext(EXACT_CONTEXT):
        cents = int(canonical * 100)
        result = cents * (offset + units) // factor - cents * offset // factor
        return Decimal(result).scaleb(-2)


def _slice_result(row: UnitReturnSlice) -> UnitReturnSliceResult:
    return UnitReturnSliceResult(
        id=row.id,
        position=row.position,
        original_entry_id=row.original_entry_id,
        lot_id=row.lot_id,
        pack_ordinal_start=row.pack_ordinal_start,
        pack_count=row.pack_count,
        unit_offset=row.unit_offset,
        base_units=row.base_units,
        original_pack_cost=row.original_pack_cost,
        cost_amount=row.cost_amount,
        money_version=row.money_version,
        currency=row.currency,
    )


def _result(command: UnitReturnCommand, slices: tuple[UnitReturnSlice, ...]) -> UnitReturnResult:
    return UnitReturnResult(
        command_id=command.id,
        tenant_id=command.tenant_id,
        branch_id=command.branch_id,
        catalog_id=command.catalog_id,
        actor_id=command.actor_id,
        original_line_id=command.original_line_id,
        state=command.state,
        packaging=UnitPackaging(
            version=command.version,
            unit=command.unit,
            units_per_pack=command.units_per_pack,
            dispense_step=command.dispense_step,
        ),
        sale_format=command.sale_format,
        quantity=command.quantity,
        allocation_offset=command.allocation_offset,
        base_units=command.base_units,
        original_retail_unit_price=command.original_retail_unit_price,
        gross_amount=command.gross_amount,
        discount_amount=command.discount_amount,
        net_amount=command.net_amount,
        cost_amount=command.cost_amount,
        money_version=command.money_version,
        currency=command.currency,
        slices=tuple(_slice_result(row) for row in slices),
    )


class UnitReturnService:
    def __init__(self, repo: UnitReturnRepository) -> None:
        self.repo = repo

    async def return_received_line(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        operation_id: UUID,
        original_line_id: UUID,
        quantity: int,
    ) -> UnitReturnResult:
        if (
            not all(
                isinstance(value, UUID)
                for value in (tenant_id, branch_id, actor_id, operation_id, original_line_id)
            )
            or not isinstance(allowed_branch_ids, set)
            or branch_id not in allowed_branch_ids
            or not all(isinstance(value, UUID) for value in allowed_branch_ids)
        ):
            raise PermissionDeniedError("Branch refund access denied")
        if type(quantity) is not int or not 0 < quantity <= MAX_BASE_UNITS:
            raise BusinessRuleError("Return quantity must be a positive whole number")
        await self.repo.assert_actor(tenant_id, branch_id, actor_id)
        fingerprint = return_fingerprint(tenant_id, branch_id, original_line_id, quantity)
        async with self.repo.session.begin_nested():
            await self.repo.lock_operation(tenant_id, operation_id)
            existing = await self.repo.get_command(tenant_id, operation_id)
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise ConflictError("Return operation was used for a different request")
                return _result(existing, await self.repo.get_slices(tenant_id, existing.id))
            line = await self.repo.lock_original_line(tenant_id, original_line_id)
            if line is None or line.branch_id != branch_id:
                raise NotFoundError("Original prepared line not found")
            source = await self.repo.get_original_command(tenant_id, line.command_id)
            if (
                source is None
                or source.stock_source_kind != "received"
                or (source.branch_id, source.catalog_id) != (branch_id, line.catalog_id)
            ):
                raise BusinessRuleError("Only received stock can be returned to quarantine")
            packaging = UnitPackaging(
                version=line.version,
                unit=line.unit,
                units_per_pack=line.units_per_pack,
                dispense_step=line.dispense_step,
            )
            offset = await self.repo.returned_quantity(tenant_id, line.id)
            if offset + quantity > line.quantity:
                raise BusinessRuleError("Return exceeds original remaining quantity")
            if line.sale_format == "unit" and (
                quantity % packaging.dispense_step or offset % packaging.dispense_step
            ):
                raise BusinessRuleError("Return must respect original dispense step")
            factor = packaging.units_per_pack if line.sale_format == "pack" else 1
            start, stop = offset * factor, (offset + quantity) * factor
            entries = await self.repo.get_original_entries(tenant_id, line.stock_operation_id)
            slices = self._allocate_slices(
                entries,
                tenant_id,
                branch_id,
                line.catalog_id,
                actor_id,
                operation_id,
                packaging.units_per_pack,
                start,
                stop,
                line.base_units,
                line.original_cost_amount,
            )
            net = allocate_unit_amount(
                line.net_amount, total_units=line.quantity, offset=offset, quantity=quantity
            ).amount
            with localcontext(EXACT_CONTEXT):
                gross = line.original_retail_unit_price * quantity
                discount = gross - net
                cost = sum((row.cost_amount for row in slices), Decimal("0.00"))
            command = UnitReturnCommand(
                id=operation_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                catalog_id=line.catalog_id,
                actor_id=actor_id,
                original_line_id=line.id,
                request_hash=fingerprint,
                state="prepared_quarantine",
                version=line.version,
                unit=line.unit,
                units_per_pack=line.units_per_pack,
                dispense_step=line.dispense_step,
                sale_format=line.sale_format,
                quantity=quantity,
                allocation_offset=offset,
                base_units=stop - start,
                original_retail_unit_price=line.original_retail_unit_price,
                gross_amount=gross,
                discount_amount=discount,
                net_amount=net,
                cost_amount=cost,
                money_version=line.money_version,
                currency=line.currency,
                created_by=actor_id,
                updated_by=actor_id,
            )
            result = _result(command, slices)
            await self.repo.insert_command(command)
            await self.repo.insert_slices(slices)
            return result

    @staticmethod
    def _allocate_slices(
        entries: tuple[UnitStockEntry, ...],
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        factor: int,
        start: int,
        stop: int,
        original_base_units: int,
        original_cost: Decimal,
    ) -> tuple[UnitReturnSlice, ...]:
        result: list[UnitReturnSlice] = []
        cursor = 0
        total_cost = Decimal("0.00")
        for entry in entries:
            n, ordinal, offset = (
                entry.cost_base_units,
                entry.cost_pack_ordinal_start,
                entry.cost_unit_offset,
            )
            cost, entry_cost = entry.cost_original_pack_cost, entry.cost_amount
            if n is None or ordinal is None or offset is None or cost is None or entry_cost is None:
                raise BusinessRuleError("Original physical cost snapshot is incomplete")
            if entry.cost_version != 1 or n <= 0 or ordinal < 1 or not 0 <= offset < factor:
                raise BusinessRuleError("Original physical cost snapshot is invalid")
            if (entry.tenant_id, entry.branch_id, entry.catalog_id) != (
                tenant_id,
                branch_id,
                catalog_id,
            ):
                raise BusinessRuleError("Original physical cost scope differs")
            if entry_cost != _physical_cost(cost, factor, offset, n):
                raise BusinessRuleError("Original physical cost allocation differs")
            with localcontext(EXACT_CONTEXT):
                total_cost += entry_cost
            left, right = max(start, cursor), min(stop, cursor + n)
            if left < right:
                physical_offset = offset + left - cursor
                units = right - left
                unit_offset = physical_offset % factor
                position = len(result) + 1
                result.append(
                    UnitReturnSlice(
                        id=uuid5(
                            NAMESPACE_URL,
                            f"aurum-unit-return-slice:{tenant_id}:{operation_id}:{position}",
                        ),
                        tenant_id=tenant_id,
                        branch_id=branch_id,
                        catalog_id=catalog_id,
                        command_id=operation_id,
                        position=position,
                        original_entry_id=entry.id,
                        lot_id=entry.lot_id,
                        pack_ordinal_start=ordinal + physical_offset // factor,
                        pack_count=(unit_offset + units + factor - 1) // factor,
                        unit_offset=unit_offset,
                        base_units=units,
                        original_pack_cost=cost,
                        cost_amount=_physical_cost(cost, factor, physical_offset, units),
                        money_version=1,
                        currency="TJS",
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                )
            cursor += n
        if cursor != original_base_units or total_cost != original_cost:
            raise BusinessRuleError("Original physical cost slices are incomplete")
        return tuple(result)
