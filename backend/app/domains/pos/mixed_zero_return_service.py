"""Review and receive exactly zero historical value using the mixed quarantine journal."""

import json
import re
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, BusinessRuleError, ConflictError
from app.domains.pos.mixed_cash_refund_preview_repository import MixedCashRefundPreviewRepository
from app.domains.pos.mixed_cash_refund_preview_service import MixedCashRefundPreviewService
from app.domains.pos.mixed_cash_refund_review import mixed_refund_revision
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import (
    LegacyReturnSelection,
    MixedReturnSelection,
    plan_mixed_return,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_schemas import MixedReturnJournalResult
from app.domains.pos.mixed_return_journal_service import (
    MixedReturnJournalService,
    mixed_return_fingerprint,
)
from app.domains.pos.mixed_zero_return_models import MixedZeroReturn
from app.domains.pos.mixed_zero_return_repository import MixedZeroReturnRepository
from app.domains.pos.mixed_zero_return_schemas import (
    MixedLegacyZeroReturnLine,
    MixedPhysicalZeroReturnLine,
    MixedZeroReturnNotFound,
    MixedZeroReturnRead,
    MixedZeroReturnResult,
)
from app.domains.pos.physical_cash_refund_service import _normalize_reason
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_zero_return_service import _assert_reason_policy


def mixed_zero_return_fingerprint(
    tenant_id: UUID,
    actor_id: UUID,
    parent_sale_id: UUID,
    selections: tuple[MixedReturnSelection, ...],
    *,
    review_hash: str,
    reason_code: str | None = None,
    comment: str | None = None,
) -> str:
    if not isinstance(review_hash, str) or re.fullmatch(r"[0-9a-f]{64}", review_hash) is None:
        raise BusinessRuleError("Return review hash is invalid")
    reason_code, comment = _normalize_reason(reason_code, comment)
    payload = {
        "version": 1,
        "kind": "mixed_zero_return",
        "tenant_id": str(tenant_id),
        "actor_id": str(actor_id),
        "parent_sale_id": str(parent_sale_id),
        "journal_request_hash": mixed_return_fingerprint(
            tenant_id, actor_id, parent_sale_id, selections
        ),
        "reason_code": reason_code,
        "comment_hash": (
            sha256(comment.encode("utf-8")).hexdigest() if comment is not None else None
        ),
        "review_hash": review_hash,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _acknowledgment(journal: MixedReturnJournalResult) -> MixedZeroReturnResult:
    return MixedZeroReturnResult(
        operation_id=journal.operation_id,
        parent_sale_id=journal.parent_sale_id,
        tenant_id=journal.tenant_id,
        branch_id=journal.branch_id,
        actor_id=journal.actor_id,
        total_amount=Decimal("0.00"),
        lines=tuple(
            (
                MixedLegacyZeroReturnLine(
                    line_id=line.line_id, quantity=line.quantity, offset=line.offset
                )
                if line.inventory_kind == "legacy_pack"
                else MixedPhysicalZeroReturnLine(
                    line_id=line.line_id,
                    sale_format=line.sale_format,
                    quantity=line.quantity,
                    base_units=line.base_units,
                    offset=line.offset,
                )
            )
            for line in journal.lines
        ),
    )


class MixedZeroReturnService:
    def __init__(self, repository: MixedZeroReturnRepository) -> None:
        self.repository = repository

    async def _history(
        self,
        tenant_id: UUID,
        parent_sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> tuple[MixedCheckoutResultV4, dict[UUID, Decimal], dict[UUID, int]]:
        preview = MixedCashRefundPreviewService(
            MixedCashRefundPreviewRepository(self.repository.session)
        )
        return await preview._history(tenant_id, parent_sale_id, actor_id, allowed_branch_ids)

    async def _saved(
        self, marker: MixedZeroReturn, original: MixedCheckoutResultV4
    ) -> MixedZeroReturnResult:
        journal = MixedReturnJournalRepository(self.repository.session)
        command = await journal.get_command(marker.tenant_id, marker.id)
        rows = await self.repository.lines(marker.tenant_id, marker.id)
        try:
            if command is None:
                raise ValueError("Missing quarantine journal")
            received = MixedReturnJournalResult.model_validate(command.result_payload)
            result = MixedZeroReturnResult.model_validate(marker.result_payload)
            selections: tuple[MixedReturnSelection, ...] = tuple(
                (
                    LegacyReturnSelection(line.line_id, line.quantity)
                    if line.inventory_kind == "legacy_pack"
                    else PhysicalReturnSelection(line.line_id, line.sale_format, line.quantity)
                )
                for line in received.lines
            )
            plan = plan_mixed_return(
                original,
                selections,
                returned_legacy_quantities={
                    line.line_id: line.offset
                    for line in received.lines
                    if line.inventory_kind == "legacy_pack"
                },
                returned_base_units={
                    line.line_id: line.offset
                    for line in received.lines
                    if line.inventory_kind == "physical_unit"
                },
            )
            if (
                received.total_amount != 0
                or command.total_amount != 0
                or command.physical_cost_amount != received.physical_cost_amount
                or command.state != received.state
                or command.parent_checkout_id != original.operation_id
                or command.stock_command_id != original.stock_command_id
                or marker.parent_sale_id != original.sale_id
                or marker.tenant_id != original.tenant_id
                or marker.branch_id != original.branch_id
                or result != _acknowledgment(received)
                or (
                    result.tenant_id,
                    result.operation_id,
                    result.parent_sale_id,
                    result.branch_id,
                    result.actor_id,
                )
                != (
                    marker.tenant_id,
                    marker.id,
                    marker.parent_sale_id,
                    marker.branch_id,
                    marker.actor_id,
                )
                or (
                    command.tenant_id,
                    command.id,
                    command.parent_sale_id,
                    command.branch_id,
                    command.actor_id,
                )
                != (
                    marker.tenant_id,
                    marker.id,
                    marker.parent_sale_id,
                    marker.branch_id,
                    marker.actor_id,
                )
                or command.request_hash
                != mixed_return_fingerprint(
                    marker.tenant_id, marker.actor_id, marker.parent_sale_id, selections
                )
                or marker.request_hash
                != mixed_zero_return_fingerprint(
                    marker.tenant_id,
                    marker.actor_id,
                    marker.parent_sale_id,
                    selections,
                    review_hash=marker.review_hash,
                    reason_code=marker.reason_code,
                    comment=marker.comment,
                )
                or len(rows) != len(received.lines)
                or plan.total_amount != 0
            ):
                raise ValueError("Quarantine acknowledgment scope differs")
            for row, line, allocation in zip(rows, received.lines, plan.lines, strict=True):
                if (
                    row.branch_id != marker.branch_id
                    or row.inventory_kind != line.inventory_kind
                    or row.line_id != line.line_id
                    or row.position != line.position
                    or line.position != allocation.position
                    or row.net_amount != line.net_amount
                    or row.discount_amount != line.discount_amount
                    or row.gross_amount != line.gross_amount
                    or line.net_amount != allocation.net_amount
                    or line.discount_amount != allocation.discount_amount
                    or line.gross_amount != allocation.gross_amount
                ):
                    raise ValueError("Quarantine allocation differs")
                if line.inventory_kind == "legacy_pack":
                    if (row.legacy_quantity, row.legacy_offset, row.batch_id) != (
                        line.quantity,
                        line.offset,
                        line.batch_id,
                    ):
                        raise ValueError("Legacy quarantine allocation differs")
                elif (
                    row.quantity,
                    row.offset,
                    row.base_units,
                    row.sale_format,
                    row.cost_amount,
                ) != (
                    line.quantity,
                    line.offset,
                    line.base_units,
                    line.sale_format,
                    line.cost_amount,
                ):
                    raise ValueError("Physical quarantine allocation differs")
        except (ValidationError, ValueError, BusinessRuleError) as error:
            raise AurumError("Stored mixed zero-value return is inconsistent") from error
        return result

    async def receive(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        selections: tuple[MixedReturnSelection, ...],
        expected_revision_hash: str,
        reason_code: str | None = None,
        comment: str | None = None,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> MixedZeroReturnResult:
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
            (
                LegacyReturnSelection(UUID(str(s.line_id)), s.quantity)
                if isinstance(s, LegacyReturnSelection)
                else PhysicalReturnSelection(UUID(str(s.line_id)), s.sale_format, s.quantity)
            )
            for s in selections
        )
        reason_code, comment = _normalize_reason(reason_code, comment)
        fingerprint = mixed_zero_return_fingerprint(
            tenant_id,
            actor_id,
            parent_sale_id,
            selections,
            review_hash=expected_revision_hash,
            reason_code=reason_code,
            comment=comment,
        )
        await self._history(tenant_id, parent_sale_id, actor_id, allowed_branch_ids)
        journal = MixedReturnJournalRepository(self.repository.session)
        async with self.repository.session.begin_nested():
            await journal.lock_operation(tenant_id, operation_id)
            await journal.lock_sale(tenant_id, parent_sale_id)
            original, legacy, physical = await self._history(
                tenant_id, parent_sale_id, actor_id, allowed_branch_ids
            )
            existing = await self.repository.get(tenant_id, operation_id)
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise ConflictError("Return operation was used for a different request")
                return await self._saved(existing, original)
            if await journal.get_command(tenant_id, operation_id) is not None:
                raise ConflictError("Return operation already exists without a zero-value intake")
            settings = await self.repository.get_settings_for_pos(tenant_id)
            if settings is None:
                raise AurumError("POS settings are unavailable")
            _assert_reason_policy(settings.refund_reason_mode, reason_code, comment)
            revision = mixed_refund_revision(original, legacy, physical, selections)
            if expected_revision_hash != revision:
                raise ConflictError("Refund history changed; review the return again")
            try:
                plan = plan_mixed_return(
                    original,
                    selections,
                    returned_legacy_quantities=legacy,
                    returned_base_units=physical,
                )
            except ValueError as error:
                raise BusinessRuleError(str(error)) from error
            if plan.total_amount != 0:
                raise BusinessRuleError(
                    "Quarantine without payout requires zero historical net amount"
                )
            received = await MixedReturnJournalService(journal).receive(
                tenant_id=tenant_id,
                actor_id=actor_id,
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                selections=selections,
            )
            result = _acknowledgment(received)
            await self.repository.insert(
                MixedZeroReturn(
                    tenant_id=tenant_id,
                    id=operation_id,
                    parent_sale_id=parent_sale_id,
                    branch_id=original.branch_id,
                    actor_id=actor_id,
                    request_hash=fingerprint,
                    reason_code=reason_code,
                    comment=comment,
                    review_hash=expected_revision_hash,
                    result_payload=result.model_dump(mode="json"),
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            await journal.assert_actor(tenant_id, actor_id, original.branch_id)
            return result

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> MixedZeroReturnRead:
        original, _, _ = await self._history(
            tenant_id, parent_sale_id, actor_id, allowed_branch_ids
        )
        marker = await self.repository.get(tenant_id, operation_id)
        if marker is None or marker.actor_id != actor_id or marker.parent_sale_id != parent_sale_id:
            return MixedZeroReturnNotFound(operation_id=operation_id, parent_sale_id=parent_sale_id)
        result = await self._saved(marker, original)
        await MixedReturnJournalRepository(self.repository.session).assert_actor(
            tenant_id, actor_id, original.branch_id
        )
        return result
