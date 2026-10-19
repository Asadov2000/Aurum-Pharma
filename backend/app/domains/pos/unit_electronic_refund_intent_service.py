"""Persist and recover a reviewed allocation without authorizing a refund."""

from decimal import Decimal, localcontext
from uuid import UUID

from pydantic import ValidationError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.pos.unit_electronic_refund_intent_models import UnitElectronicRefundIntent
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import (
    ElectronicRefundIntentRequest,
    SavedElectronicRefundIntent,
    SaveElectronicRefundIntentRequest,
)
from app.domains.pos.unit_electronic_refund_preview import UnitElectronicRefundPreview
from app.domains.pos.unit_electronic_refund_preview_repository import (
    UnitElectronicRefundPreviewRepository,
)
from app.domains.pos.unit_electronic_refund_preview_service import (
    UnitElectronicRefundPreviewService,
)
from app.domains.sync.integrity import canonical_json_hash


def refund_intent_request_hash(request: ElectronicRefundIntentRequest) -> str:
    return canonical_json_hash(
        {"kind": "unit_electronic_refund_review_v1", "request": request.model_dump(mode="json")}
    )


def _require_matching_preview(
    request: ElectronicRefundIntentRequest, preview: UnitElectronicRefundPreview
) -> None:
    if (
        preview.parent_sale_id != request.parent_sale_id
        or preview.total_amount != request.expected_total_amount
        or preview.revision_hash != request.expected_revision_hash
    ):
        raise ConflictError("Refund history changed; review the return again")
    if preview.total_amount <= 0 or not any(part.method != "cash" for part in preview.payments):
        raise BusinessRuleError("Saved electronic review requires a positive electronic refund")
    selected = {(item.line_id, item.sale_format, item.quantity) for item in request.selection.items}
    quoted = {(line.line_id, line.sale_format, line.quantity) for line in preview.lines}
    with localcontext(EXACT_CONTEXT):
        if (
            len(quoted) != len(preview.lines)
            or selected != quoted
            or len({part.original_payment_id for part in preview.payments}) != len(preview.payments)
            or any(part.amount <= 0 for part in preview.payments)
            or sum((line.net_amount for line in preview.lines), Decimal("0.00"))
            != preview.total_amount
            or sum((part.amount for part in preview.payments), Decimal("0.00"))
            != preview.total_amount
        ):
            raise AurumError("Stored electronic refund review is inconsistent")


class UnitElectronicRefundIntentService:
    def __init__(self, repository: UnitElectronicRefundIntentRepository) -> None:
        self.repository = repository
        self.identity = UnitCartRepository(repository.session)
        self.authority = PhysicalReturnJournalRepository(repository.session)

    @staticmethod
    def _scope(tenant_id: UUID, actor_id: UUID, branches: set[UUID] | None) -> None:
        if not isinstance(tenant_id, UUID) or not isinstance(actor_id, UUID):
            raise BusinessRuleError("Refund review scope must use UUIDs")
        if branches is not None and (
            not isinstance(branches, set)
            or not all(isinstance(branch, UUID) for branch in branches)
        ):
            raise PermissionDeniedError("Refund review branch access is invalid")

    async def _access(
        self, intent: UnitElectronicRefundIntent, actor_id: UUID, branches: set[UUID] | None
    ) -> None:
        if intent.actor_id != actor_id or (
            branches is not None and intent.branch_id not in branches
        ):
            raise PermissionDeniedError("Saved refund review actor or branch access denied")
        await self.authority.assert_actor(intent.tenant_id, actor_id, intent.branch_id)

    async def save(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        request: SaveElectronicRefundIntentRequest,
    ) -> SavedElectronicRefundIntent:
        request = SaveElectronicRefundIntentRequest.model_validate(request)
        self._scope(tenant_id, actor_id, allowed_branch_ids)
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending changes before saving a refund review")
        await self.identity.assert_actor(tenant_id, actor_id)
        async with session.begin_nested():
            await self.repository.lock_operation(tenant_id, request.operation_id)
            intent = await self.repository.get(tenant_id, request.operation_id)
            request_hash = refund_intent_request_hash(request.request)
            if intent is None:
                preview = await UnitElectronicRefundPreviewService(
                    UnitElectronicRefundPreviewRepository(session)
                ).quote(
                    tenant_id=tenant_id,
                    parent_sale_id=request.request.parent_sale_id,
                    actor_id=actor_id,
                    allowed_branch_ids=allowed_branch_ids,
                    request=request.request.selection,
                )
                _require_matching_preview(request.request, preview)
                intent = UnitElectronicRefundIntent(
                    tenant_id=tenant_id,
                    id=request.operation_id,
                    parent_sale_id=preview.parent_sale_id,
                    branch_id=preview.branch_id,
                    actor_id=actor_id,
                    created_by=actor_id,
                    request_hash=request_hash,
                    request_payload=request.request.model_dump(mode="json"),
                    result_payload=preview.model_dump(mode="json"),
                )
                await self.repository.insert(intent)
            await self._access(intent, actor_id, allowed_branch_ids)
            if intent.request_hash != request_hash:
                raise ConflictError("Refund review operation was used for another request")
            return self._result(intent)

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
    ) -> SavedElectronicRefundIntent:
        self._scope(tenant_id, actor_id, allowed_branch_ids)
        if not isinstance(parent_sale_id, UUID) or not isinstance(operation_id, UUID):
            raise BusinessRuleError("Refund review identifiers must use UUIDs")
        # Recovery must not flush unrelated pending ORM edits or recalculate history.
        with self.repository.session.no_autoflush:
            await self.identity.assert_actor(tenant_id, actor_id)
            intent = await self.repository.get(tenant_id, operation_id)
            if intent is None or intent.parent_sale_id != parent_sale_id:
                raise NotFoundError("Saved electronic refund review not found")
            await self._access(intent, actor_id, allowed_branch_ids)
            return self._result(intent)

    @staticmethod
    def _result(intent: UnitElectronicRefundIntent) -> SavedElectronicRefundIntent:
        try:
            request = ElectronicRefundIntentRequest.model_validate(intent.request_payload)
            preview = UnitElectronicRefundPreview.model_validate(intent.result_payload)
            _require_matching_preview(request, preview)
            if (
                preview.parent_sale_id != intent.parent_sale_id
                or preview.branch_id != intent.branch_id
                or refund_intent_request_hash(request) != intent.request_hash
            ):
                raise ValueError("Stored review scope differs")
            return SavedElectronicRefundIntent(
                operation_id=intent.id,
                saved_at=intent.created_at,
                request=request,
                preview=preview,
            )
        except (ValidationError, ValueError, ConflictError, BusinessRuleError):
            raise AurumError("Stored electronic refund review is inconsistent") from None
