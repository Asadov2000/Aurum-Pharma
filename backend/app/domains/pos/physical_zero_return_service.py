"""Accept exactly zero-value historical goods without cash or sale mutations."""

import json
import re
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.domains.pos.physical_cash_refund_review import physical_refund_revision
from app.domains.pos.physical_cash_refund_service import _normalize_reason
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection, plan_physical_return
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_return_journal_schemas import PhysicalReturnJournalResult
from app.domains.pos.physical_return_journal_service import (
    PhysicalReturnJournalService,
    physical_return_fingerprint,
)
from app.domains.pos.physical_zero_return_models import PhysicalZeroReturn
from app.domains.pos.physical_zero_return_repository import PhysicalZeroReturnRepository
from app.domains.pos.physical_zero_return_schemas import (
    PhysicalZeroReturnLine,
    PhysicalZeroReturnNotFound,
    PhysicalZeroReturnRead,
    PhysicalZeroReturnResult,
)


def physical_zero_return_fingerprint(
    tenant_id: UUID,
    actor_id: UUID,
    parent_sale_id: UUID,
    selections: tuple[PhysicalReturnSelection, ...],
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
        "kind": "physical_zero_return",
        "tenant_id": str(tenant_id),
        "actor_id": str(actor_id),
        "parent_sale_id": str(parent_sale_id),
        "journal_request_hash": physical_return_fingerprint(
            tenant_id, actor_id, parent_sale_id, selections
        ),
        "reason_code": reason_code,
        "comment_hash": (
            sha256(comment.encode("utf-8")).hexdigest() if comment is not None else None
        ),
        "review_hash": review_hash,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _acknowledgment(journal: PhysicalReturnJournalResult) -> PhysicalZeroReturnResult:
    return PhysicalZeroReturnResult(
        operation_id=journal.operation_id,
        parent_sale_id=journal.parent_sale_id,
        tenant_id=journal.tenant_id,
        branch_id=journal.branch_id,
        actor_id=journal.actor_id,
        total_amount=Decimal("0.00"),
        lines=tuple(
            PhysicalZeroReturnLine(
                line_id=line.line_id, base_units=line.base_units, offset=line.offset
            )
            for line in journal.lines
        ),
    )


def _assert_reason_policy(mode: str, reason_code: str | None, comment: str | None) -> None:
    if mode == "off" and (reason_code is not None or comment is not None):
        raise BusinessRuleError("Refund reason fields are disabled")
    if mode in {"required", "required_with_text"} and reason_code is None:
        raise BusinessRuleError("Refund reason is required")
    if mode == "required_with_text" and comment is None:
        raise BusinessRuleError("Refund comment is required")


class PhysicalZeroReturnService:
    def __init__(self, repository: PhysicalZeroReturnRepository) -> None:
        self.repository = repository

    async def _saved(self, marker: PhysicalZeroReturn) -> PhysicalZeroReturnResult:
        journal = PhysicalReturnJournalRepository(self.repository.session)
        command = await journal.get_command(marker.tenant_id, marker.id)
        try:
            if command is None:
                raise ValueError("Missing quarantine journal")
            received = PhysicalReturnJournalResult.model_validate(command.result_payload)
            result = PhysicalZeroReturnResult.model_validate(marker.result_payload)
            if (
                received.total_amount != 0
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
            ):
                raise ValueError("Quarantine acknowledgment scope differs")
        except (ValidationError, ValueError) as error:
            raise AurumError("Stored zero-value return is inconsistent") from error
        return result

    async def receive(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
        selections: tuple[PhysicalReturnSelection, ...],
        expected_revision_hash: str,
        reason_code: str | None = None,
        comment: str | None = None,
    ) -> PhysicalZeroReturnResult:
        if not all(
            isinstance(v, UUID) for v in (tenant_id, actor_id, operation_id, parent_sale_id)
        ):
            raise BusinessRuleError("Typed return identifiers are required")
        tenant_id, actor_id, operation_id, parent_sale_id = (
            UUID(str(v)) for v in (tenant_id, actor_id, operation_id, parent_sale_id)
        )
        if type(selections) is not tuple or any(
            type(s) is not PhysicalReturnSelection or not isinstance(s.line_id, UUID)
            for s in selections
        ):
            raise BusinessRuleError("Typed return selections are required")
        selections = tuple(
            PhysicalReturnSelection(UUID(str(s.line_id)), s.sale_format, s.quantity)
            for s in selections
        )
        reason_code, comment = _normalize_reason(reason_code, comment)
        fingerprint = physical_zero_return_fingerprint(
            tenant_id,
            actor_id,
            parent_sale_id,
            selections,
            review_hash=expected_revision_hash,
            reason_code=reason_code,
            comment=comment,
        )
        journal = PhysicalReturnJournalRepository(self.repository.session)
        checkout = await journal.get_checkout(tenant_id, parent_sale_id)
        if checkout is None:
            raise NotFoundError("Original physical checkout not found")
        await journal.assert_actor(tenant_id, actor_id, checkout.branch_id)
        async with self.repository.session.begin_nested():
            await journal.lock_operation(tenant_id, operation_id)
            parent = await journal.lock_sale(tenant_id, parent_sale_id)
            await journal.assert_actor(tenant_id, actor_id, checkout.branch_id)
            if parent is None or parent.status != "completed" or parent.sale_type != "sale":
                raise BusinessRuleError("Completed original physical sale required")
            existing = await self.repository.get(tenant_id, operation_id)
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise ConflictError("Return operation was used for a different request")
                return await self._saved(existing)
            if await journal.get_command(tenant_id, operation_id) is not None:
                raise ConflictError("Return operation already exists without a zero-value intake")
            settings = await self.repository.get_settings_for_pos(tenant_id)
            if settings is None:
                raise AurumError("POS settings are unavailable")
            _assert_reason_policy(settings.refund_reason_mode, reason_code, comment)
            original = PhysicalCheckoutResultV3.model_validate(checkout.result_payload)
            cursors = await journal.cursors(tenant_id, original.stock_command_id)
            if expected_revision_hash != physical_refund_revision(original, cursors, selections):
                raise ConflictError("Refund history changed; review the return again")
            try:
                plan = plan_physical_return(original, selections, returned_base_units=cursors)
            except ValueError as error:
                raise BusinessRuleError(str(error)) from error
            if plan.total_amount != 0:
                raise BusinessRuleError(
                    "Quarantine without payout requires zero historical net amount"
                )
            received = await PhysicalReturnJournalService(journal).receive(
                tenant_id=tenant_id,
                actor_id=actor_id,
                operation_id=operation_id,
                parent_sale_id=parent_sale_id,
                selections=selections,
            )
            result = _acknowledgment(received)
            await self.repository.insert(
                PhysicalZeroReturn(
                    tenant_id=tenant_id,
                    id=operation_id,
                    parent_sale_id=parent_sale_id,
                    branch_id=parent.branch_id,
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
            await journal.assert_actor(tenant_id, actor_id, parent.branch_id)
            return result

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        parent_sale_id: UUID,
    ) -> PhysicalZeroReturnRead:
        journal = PhysicalReturnJournalRepository(self.repository.session)
        checkout = await journal.get_checkout(tenant_id, parent_sale_id)
        if checkout is None:
            raise NotFoundError("Original physical checkout not found")
        await journal.assert_actor(tenant_id, actor_id, checkout.branch_id)
        marker = await self.repository.get(tenant_id, operation_id)
        if marker is None or marker.actor_id != actor_id or marker.parent_sale_id != parent_sale_id:
            return PhysicalZeroReturnNotFound(
                operation_id=operation_id, parent_sale_id=parent_sale_id
            )
        result = await self._saved(marker)
        await journal.assert_actor(tenant_id, actor_id, checkout.branch_id)
        return result
