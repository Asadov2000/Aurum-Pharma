"""Prepare both mixed-sale stock kinds atomically without cash or restocking."""

import json
from decimal import Decimal, localcontext
from hashlib import sha256
from uuid import UUID

from pydantic import TypeAdapter

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.pos.mixed_documents import LegacyResultLineV4, MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import (
    LegacyReturnAllocation,
    LegacyReturnSelection,
    MixedReturnPlan,
    MixedReturnSelection,
    plan_mixed_return,
)
from app.domains.pos.mixed_return_journal_models import (
    MixedReturnCommand,
    MixedReturnLine,
    MixedReturnSlice,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_schemas import (
    LegacyReturnJournalLine,
    MixedPhysicalReturnJournalLine,
    MixedReturnJournalResult,
)
from app.domains.pos.physical_documents import PhysicalResultLineV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_return_journal_service import PhysicalReturnJournalService
from app.domains.pos.unit_contracts import EXACT_CONTEXT, LegacyQuantity


def mixed_return_fingerprint(
    tenant_id: UUID,
    actor_id: UUID,
    parent_sale_id: UUID,
    selections: tuple[MixedReturnSelection, ...],
) -> str:
    if type(selections) is not tuple or not 1 <= len(selections) <= 200:
        raise BusinessRuleError("Return requires between 1 and 200 lines")
    if any(
        type(s) not in (LegacyReturnSelection, PhysicalReturnSelection)
        or type(s.line_id) is not UUID
        for s in selections
    ) or len({s.line_id for s in selections}) != len(selections):
        raise BusinessRuleError("Return selections must identify unique valid lines")
    values: list[dict[str, object]] = []
    for selection in sorted(selections, key=lambda s: str(s.line_id)):
        if isinstance(selection, LegacyReturnSelection):
            if type(selection.quantity) is not Decimal:
                raise BusinessRuleError("Legacy quantity must be a Decimal")
            try:
                quantity = TypeAdapter(LegacyQuantity).validate_python(selection.quantity)
            except ValueError as error:
                raise BusinessRuleError("Invalid legacy return quantity") from error
            values.append(
                {
                    "inventory_kind": "legacy_pack",
                    "line_id": str(selection.line_id),
                    "quantity": format(quantity, ".3f"),
                }
            )
        else:
            if (
                type(selection.quantity) is not int
                or not 1 <= selection.quantity <= MAX_BASE_UNITS
                or selection.sale_format not in ("pack", "blister", "unit")
            ):
                raise BusinessRuleError("Invalid physical return selection")
            values.append(
                {
                    "inventory_kind": "physical_unit",
                    "line_id": str(selection.line_id),
                    "quantity": selection.quantity,
                    "sale_format": selection.sale_format,
                }
            )
    payload = {
        "version": 1,
        "kind": "mixed_return_quarantine",
        "tenant_id": str(tenant_id),
        "actor_id": str(actor_id),
        "parent_sale_id": str(parent_sale_id),
        "selections": values,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class MixedReturnJournalService:
    def __init__(self, repo: MixedReturnJournalRepository) -> None:
        self.repo = repo

    async def receive(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        selections: tuple[MixedReturnSelection, ...],
    ) -> MixedReturnJournalResult:
        if not all(
            isinstance(v, UUID) for v in (tenant_id, actor_id, operation_id, parent_sale_id)
        ):
            raise BusinessRuleError("Typed return identifiers are required")
        tenant_id, actor_id, operation_id, parent_sale_id = (
            UUID(str(v)) for v in (tenant_id, actor_id, operation_id, parent_sale_id)
        )
        if type(selections) is not tuple or any(
            type(s) not in (LegacyReturnSelection, PhysicalReturnSelection)
            or not isinstance(s.line_id, UUID)
            for s in selections
        ):
            raise BusinessRuleError("Typed mixed return selections are required")
        selections = tuple(
            LegacyReturnSelection(UUID(str(s.line_id)), s.quantity)
            if isinstance(s, LegacyReturnSelection)
            else PhysicalReturnSelection(UUID(str(s.line_id)), s.sale_format, s.quantity)
            for s in selections
        )
        fingerprint = mixed_return_fingerprint(tenant_id, actor_id, parent_sale_id, selections)
        # Authenticate before taking locks, then recheck the exact saved branch.
        await self.repo.assert_identity(tenant_id, actor_id)
        async with self.repo.session.begin_nested():
            await self.repo.lock_operation(tenant_id, operation_id)
            existing = await self.repo.get_command(tenant_id, operation_id)
            if existing is not None:
                await self.repo.assert_actor(tenant_id, actor_id, existing.branch_id)
                if existing.request_hash != fingerprint:
                    raise ConflictError("Return operation was used for a different request")
                return MixedReturnJournalResult.model_validate(existing.result_payload)
            checkout = await self.repo.get_checkout(tenant_id, parent_sale_id)
            if checkout is None:
                raise NotFoundError("Original mixed checkout not found")
            await self.repo.assert_actor(tenant_id, actor_id, checkout.branch_id)
            sale = await self.repo.lock_sale(tenant_id, parent_sale_id)
            await self.repo.assert_actor(tenant_id, actor_id, checkout.branch_id)
            if (
                sale is None
                or sale.status != "completed"
                or sale.sale_type != "sale"
                or sale.parent_sale_id is not None
                or sale.is_test
                or sale.currency != "TJS"
            ):
                raise BusinessRuleError("A completed original mixed sale is required")
            original = MixedCheckoutResultV4.model_validate(checkout.result_payload)
            if (
                original.tenant_id,
                original.sale_id,
                original.operation_id,
                original.stock_command_id,
                original.branch_id,
                original.register_id,
                original.shift_id,
                original.cashier_id,
                original.receipt_number,
                original.receipt_sequence,
                original.total_amount,
                original.is_test,
            ) != (
                tenant_id,
                parent_sale_id,
                checkout.id,
                checkout.stock_command_id,
                sale.branch_id,
                sale.register_id,
                sale.shift_id,
                sale.cashier_user_id,
                sale.receipt_number,
                sale.receipt_seq,
                sale.total_amount,
                sale.is_test,
            ) or (
                checkout.branch_id,
                checkout.register_id,
                checkout.shift_id,
                checkout.cashier_user_id,
            ) != (sale.branch_id, sale.register_id, sale.shift_id, sale.cashier_user_id):
                raise BusinessRuleError("Original mixed checkout scope differs")
            legacy_cursors, physical_cursors = await self.repo.cursors(tenant_id, parent_sale_id)
            try:
                plan = plan_mixed_return(
                    original,
                    selections,
                    returned_legacy_quantities=legacy_cursors,
                    returned_base_units=physical_cursors,
                )
            except ValueError as error:
                raise BusinessRuleError(str(error)) from error
            result_lines, persisted_lines, persisted_slices = await self._lines(
                tenant_id=tenant_id,
                actor_id=actor_id,
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                branch_id=checkout.branch_id,
                stock_command_id=checkout.stock_command_id,
                original=original,
                plan=plan,
            )
            with localcontext(EXACT_CONTEXT):
                cost_total = sum(
                    (
                        line.cost_amount
                        for line in result_lines
                        if isinstance(line, MixedPhysicalReturnJournalLine)
                    ),
                    Decimal("0.00"),
                )
            result = MixedReturnJournalResult(
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                tenant_id=tenant_id,
                branch_id=checkout.branch_id,
                actor_id=actor_id,
                lines=tuple(result_lines),
                total_amount=plan.total_amount,
                physical_cost_amount=cost_total,
            )
            await self.repo.assert_actor(tenant_id, actor_id, checkout.branch_id)
            await self.repo.insert(
                MixedReturnCommand(
                    tenant_id=tenant_id,
                    id=operation_id,
                    parent_sale_id=parent_sale_id,
                    parent_checkout_id=checkout.id,
                    stock_command_id=checkout.stock_command_id,
                    branch_id=checkout.branch_id,
                    actor_id=actor_id,
                    request_hash=fingerprint,
                    state=result.state,
                    total_amount=result.total_amount,
                    physical_cost_amount=result.physical_cost_amount,
                    result_payload=result.model_dump(mode="json"),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                tuple(persisted_lines),
                tuple(persisted_slices),
            )
            return result

    async def _lines(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        branch_id: UUID,
        stock_command_id: UUID,
        original: MixedCheckoutResultV4,
        plan: MixedReturnPlan,
    ) -> tuple[
        tuple[LegacyReturnJournalLine | MixedPhysicalReturnJournalLine, ...],
        tuple[MixedReturnLine, ...],
        tuple[MixedReturnSlice, ...],
    ]:
        result_lines: list[LegacyReturnJournalLine | MixedPhysicalReturnJournalLine] = []
        persisted_lines: list[MixedReturnLine] = []
        persisted_slices: list[MixedReturnSlice] = []
        originals = {line.id: line for line in original.lines}
        for allocation in plan.lines:
            original_line = originals[allocation.line_id]
            if isinstance(allocation, LegacyReturnAllocation):
                if not isinstance(original_line, LegacyResultLineV4):
                    raise BusinessRuleError("Original legacy source kind differs")
                legacy = await self.repo.legacy_source(
                    tenant_id, parent_sale_id, allocation.line_id
                )
                if legacy is None or legacy.parent_sale_item_id is not None or (
                    legacy.catalog_id,
                    legacy.batch_id,
                    legacy.qty,
                    legacy.unit_price,
                    legacy.total_price,
                    legacy.discount_amount,
                    legacy.currency,
                ) != (
                    original_line.catalog_id,
                    original_line.batch_id,
                    original_line.quantity,
                    original_line.original_retail_unit_price,
                    original_line.net_amount,
                    original_line.discount_amount,
                    "TJS",
                ):
                    raise BusinessRuleError("Original legacy source line differs")
                result_lines.append(
                    LegacyReturnJournalLine(
                        line_id=allocation.line_id,
                        position=allocation.position,
                        batch_id=legacy.batch_id,
                        quantity=allocation.quantity,
                        offset=allocation.offset,
                        net_amount=allocation.net_amount,
                        discount_amount=allocation.discount_amount,
                        gross_amount=allocation.gross_amount,
                    )
                )
                row = MixedReturnLine(
                    inventory_kind="legacy_pack",
                    catalog_id=legacy.catalog_id,
                    batch_id=legacy.batch_id,
                    legacy_quantity=allocation.quantity,
                    legacy_offset=allocation.offset,
                )
            else:
                if not isinstance(original_line, PhysicalResultLineV3):
                    raise BusinessRuleError("Original physical source kind differs")
                source = await self.repo.source_line(
                    tenant_id, stock_command_id, allocation.line_id
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
                slices = PhysicalReturnJournalService._slices(
                    await self.repo.movements(
                        tenant_id, stock_command_id, allocation.line_id
                    ),
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    catalog_id=source.catalog_id,
                    factor=original_line.packaging.units_per_pack,
                    original_units=original_line.base_units,
                    offset=allocation.offset,
                    units=allocation.base_units,
                )
                with localcontext(EXACT_CONTEXT):
                    cost = sum((s.cost_amount for s in slices), Decimal("0.00"))
                result_lines.append(
                    MixedPhysicalReturnJournalLine(
                        line_id=allocation.line_id,
                        position=allocation.position,
                        sale_format=allocation.sale_format,
                        quantity=allocation.quantity,
                        base_units=allocation.base_units,
                        offset=allocation.offset,
                        net_amount=allocation.net_amount,
                        discount_amount=allocation.discount_amount,
                        gross_amount=allocation.gross_amount,
                        cost_amount=cost,
                        slices=slices,
                    )
                )
                row = MixedReturnLine(
                    inventory_kind="physical_unit",
                    catalog_id=source.catalog_id,
                    base_units=allocation.base_units,
                    offset=allocation.offset,
                    sale_format=allocation.sale_format,
                    quantity=allocation.quantity,
                    cost_amount=cost,
                )
                for position, part in enumerate(slices, 1):
                    persisted_slices.append(
                        MixedReturnSlice(
                            tenant_id=tenant_id,
                            command_id=operation_id,
                            line_id=allocation.line_id,
                            position=position,
                            branch_id=branch_id,
                            **part.model_dump(mode="python"),
                            created_by=actor_id,
                            updated_by=actor_id,
                        )
                    )
            row.tenant_id, row.command_id, row.line_id = tenant_id, operation_id, allocation.line_id
            row.position, row.branch_id = allocation.position, branch_id
            row.net_amount, row.discount_amount = allocation.net_amount, allocation.discount_amount
            row.gross_amount = allocation.gross_amount
            row.created_by, row.updated_by = actor_id, actor_id
            persisted_lines.append(row)
        return tuple(result_lines), tuple(persisted_lines), tuple(persisted_slices)
