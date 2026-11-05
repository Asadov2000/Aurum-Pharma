"""Receive historical physical goods into quarantine without changing stock or cash."""

import json
from decimal import Decimal, localcontext
from hashlib import sha256
from uuid import UUID

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.inventory.unit_ledger_v2_models import UnitStockMovementV2
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection, plan_physical_return
from app.domains.pos.physical_return_journal_models import (
    PhysicalReturnCommand,
    PhysicalReturnLine,
    PhysicalReturnSlice,
)
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_return_journal_schemas import (
    PhysicalReturnJournalLine,
    PhysicalReturnJournalResult,
    PhysicalReturnJournalSlice,
)
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.pos.unit_return_service import _physical_cost


def physical_return_fingerprint(
    tenant_id: UUID,
    actor_id: UUID,
    parent_sale_id: UUID,
    selections: tuple[PhysicalReturnSelection, ...],
) -> str:
    if type(selections) is not tuple or not 1 <= len(selections) <= 200:
        raise BusinessRuleError("Return requires between 1 and 200 lines")
    if any(
        type(s) is not PhysicalReturnSelection
        or type(s.line_id) is not UUID
        or type(s.quantity) is not int
        or s.quantity <= 0
        or s.sale_format not in ("pack", "blister", "unit")
        for s in selections
    ) or len({s.line_id for s in selections}) != len(selections):
        raise BusinessRuleError("Return selections must identify unique valid lines")
    payload = {
        "version": 1,
        "kind": "physical_return_quarantine",
        "tenant_id": str(tenant_id),
        "actor_id": str(actor_id),
        "parent_sale_id": str(parent_sale_id),
        "selections": [
            {"line_id": str(s.line_id), "sale_format": s.sale_format, "quantity": s.quantity}
            for s in selections
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PhysicalReturnJournalService:
    def __init__(self, repo: PhysicalReturnJournalRepository) -> None:
        self.repo = repo

    async def receive(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        selections: tuple[PhysicalReturnSelection, ...],
    ) -> PhysicalReturnJournalResult:
        if not all(
            isinstance(v, UUID) for v in (tenant_id, actor_id, operation_id, parent_sale_id)
        ):
            raise BusinessRuleError("Typed return identifiers are required")
        tenant_id, actor_id, operation_id, parent_sale_id = (
            UUID(str(v)) for v in (tenant_id, actor_id, operation_id, parent_sale_id)
        )
        if type(selections) is not tuple or any(
            type(selection) is not PhysicalReturnSelection
            or not isinstance(selection.line_id, UUID)
            for selection in selections
        ):
            raise BusinessRuleError("Typed physical return selections are required")
        selections = tuple(
            PhysicalReturnSelection(UUID(str(s.line_id)), s.sale_format, s.quantity)
            for s in selections
        )
        fingerprint = physical_return_fingerprint(tenant_id, actor_id, parent_sale_id, selections)
        checkout = await self.repo.get_checkout(tenant_id, parent_sale_id)
        if checkout is None:
            raise NotFoundError("Original physical checkout not found")
        await self.repo.assert_actor(tenant_id, actor_id, checkout.branch_id)
        async with self.repo.session.begin_nested():
            await self.repo.lock_operation(tenant_id, operation_id)
            sale = await self.repo.lock_sale(tenant_id, parent_sale_id)
            await self.repo.assert_actor(tenant_id, actor_id, checkout.branch_id)
            if sale is None or sale.status != "completed" or sale.sale_type != "sale":
                raise BusinessRuleError("A completed original physical sale is required")
            existing = await self.repo.get_command(tenant_id, operation_id)
            if existing is not None:
                await self.repo.assert_actor(tenant_id, actor_id, existing.branch_id)
                if existing.request_hash != fingerprint:
                    raise ConflictError("Return operation was used for a different request")
                return PhysicalReturnJournalResult.model_validate(existing.result_payload)
            original = PhysicalCheckoutResultV3.model_validate(checkout.result_payload)
            if (
                original.tenant_id,
                original.sale_id,
                original.stock_command_id,
                original.branch_id,
            ) != (
                tenant_id,
                parent_sale_id,
                checkout.stock_command_id,
                sale.branch_id,
            ):
                raise BusinessRuleError("Original physical checkout scope differs")
            try:
                plan = plan_physical_return(
                    original,
                    selections,
                    returned_base_units=await self.repo.cursors(
                        tenant_id, checkout.stock_command_id
                    ),
                )
            except ValueError as error:
                raise BusinessRuleError(str(error)) from error
            result_lines: list[PhysicalReturnJournalLine] = []
            persisted_lines: list[PhysicalReturnLine] = []
            persisted_slices: list[PhysicalReturnSlice] = []
            for position, allocation in enumerate(plan.lines, 1):
                source = await self.repo.source_line(
                    tenant_id, checkout.stock_command_id, allocation.line_id
                )
                original_line = next(
                    line for line in original.lines if line.id == allocation.line_id
                )
                if source is None or (
                    source.catalog_id,
                    source.packaging,
                    source.base_units,
                    source.net_amount,
                    source.discount_amount,
                ) != (
                    original_line.catalog_id,
                    original_line.packaging.model_dump(mode="json"),
                    original_line.base_units,
                    original_line.net_amount,
                    original_line.discount_amount,
                ):
                    raise BusinessRuleError("Original physical source line differs")
                slices = self._slices(
                    await self.repo.movements(
                        tenant_id, checkout.stock_command_id, allocation.line_id
                    ),
                    tenant_id=tenant_id,
                    branch_id=checkout.branch_id,
                    catalog_id=source.catalog_id,
                    factor=original_line.packaging.units_per_pack,
                    original_units=original_line.base_units,
                    offset=allocation.offset,
                    units=allocation.base_units,
                )
                with localcontext(EXACT_CONTEXT):
                    cost = sum((s.cost_amount for s in slices), Decimal("0.00"))
                result_lines.append(
                    PhysicalReturnJournalLine(
                        line_id=allocation.line_id,
                        base_units=allocation.base_units,
                        offset=allocation.offset,
                        net_amount=allocation.net_amount,
                        discount_amount=allocation.discount_amount,
                        gross_amount=allocation.gross_amount,
                        cost_amount=cost,
                        slices=slices,
                    )
                )
                persisted_lines.append(
                    PhysicalReturnLine(
                        tenant_id=tenant_id,
                        command_id=operation_id,
                        line_id=allocation.line_id,
                        position=position,
                        branch_id=checkout.branch_id,
                        catalog_id=source.catalog_id,
                        stock_command_id=checkout.stock_command_id,
                        offset=allocation.offset,
                        base_units=allocation.base_units,
                        sale_format=allocation.sale_format,
                        quantity=allocation.quantity,
                        net_amount=allocation.net_amount,
                        discount_amount=allocation.discount_amount,
                        gross_amount=allocation.gross_amount,
                        cost_amount=cost,
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                )
                for slice_position, row in enumerate(slices, 1):
                    persisted_slices.append(
                        PhysicalReturnSlice(
                            tenant_id=tenant_id,
                            command_id=operation_id,
                            line_id=allocation.line_id,
                            position=slice_position,
                            branch_id=checkout.branch_id,
                            **row.model_dump(mode="python"),
                            created_by=actor_id,
                            updated_by=actor_id,
                        )
                    )
            with localcontext(EXACT_CONTEXT):
                total_cost = sum((line.cost_amount for line in result_lines), Decimal("0.00"))
            result = PhysicalReturnJournalResult(
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                tenant_id=tenant_id,
                branch_id=checkout.branch_id,
                actor_id=actor_id,
                lines=tuple(result_lines),
                total_amount=plan.total_amount,
                total_cost_amount=total_cost,
            )
            await self.repo.assert_actor(tenant_id, actor_id, checkout.branch_id)
            await self.repo.insert(
                PhysicalReturnCommand(
                    id=operation_id,
                    tenant_id=tenant_id,
                    parent_sale_id=parent_sale_id,
                    parent_checkout_id=checkout.id,
                    stock_command_id=checkout.stock_command_id,
                    branch_id=checkout.branch_id,
                    actor_id=actor_id,
                    request_hash=fingerprint,
                    state="prepared_quarantine",
                    total_amount=result.total_amount,
                    total_cost_amount=result.total_cost_amount,
                    result_payload=result.model_dump(mode="json"),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                tuple(persisted_lines),
                tuple(persisted_slices),
            )
            return result

    @staticmethod
    def _slices(
        movements: tuple[UnitStockMovementV2, ...],
        *,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
        factor: int,
        original_units: int,
        offset: int,
        units: int,
    ) -> tuple[PhysicalReturnJournalSlice, ...]:
        cursor = 0
        result: list[PhysicalReturnJournalSlice] = []
        for movement in movements:
            n, physical_offset, cost = (
                movement.cost_base_units,
                movement.cost_unit_offset,
                movement.cost_original_pack_cost,
            )
            if (
                n is None
                or physical_offset is None
                or cost is None
                or movement.cost_version != 1
                or n != movement.units_taken
                or n <= 0
                or not 0 <= physical_offset < factor
                or (movement.tenant_id, movement.branch_id, movement.catalog_id)
                != (tenant_id, branch_id, catalog_id)
                or movement.cost_amount != _physical_cost(cost, factor, physical_offset, n)
            ):
                raise BusinessRuleError("Original physical cost snapshot is invalid")
            left, right = max(offset, cursor), min(offset + units, cursor + n)
            if left < right:
                relative = left - cursor
                result.append(
                    PhysicalReturnJournalSlice(
                        source_movement_id=movement.id,
                        lot_id=movement.lot_id,
                        source_offset=relative,
                        base_units=right - left,
                        cost_amount=_physical_cost(
                            cost, factor, physical_offset + relative, right - left
                        ),
                    )
                )
            cursor += n
        if cursor != original_units or sum(s.base_units for s in result) != units:
            raise BusinessRuleError("Original physical source slices are incomplete")
        return tuple(result)
