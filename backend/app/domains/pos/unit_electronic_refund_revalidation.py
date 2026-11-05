"""Serialize a fresh review comparison, without authorizing or reserving a payout."""

from uuid import UUID

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import SavedElectronicRefundIntent
from app.domains.pos.unit_electronic_refund_intent_service import UnitElectronicRefundIntentService
from app.domains.pos.unit_electronic_refund_preview_repository import (
    UnitElectronicRefundPreviewRepository,
)
from app.domains.pos.unit_electronic_refund_preview_service import (
    UnitElectronicRefundPreviewService,
)


class UnitElectronicRefundRevalidationService:
    def __init__(self, repository: UnitElectronicRefundIntentRepository) -> None:
        self.repository = repository

    async def revalidate_locked_review(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
    ) -> SavedElectronicRefundIntent:
        """Caller owns the transaction and its parent lock, including on failure.

        The returned snapshot remains a saved review, never an execution token.
        This read-only comparison is allowed in readonly tenants. A future writer
        must separately enforce write policy, register/shift rules and a durable
        exclusive attempt before any external action. Never wait for a terminal
        under this lock, or reuse this comparison after commit/rollback.
        """
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending changes before revalidating a refund review")
        reviews = UnitElectronicRefundIntentService(self.repository)
        # Author-scoped recovery also validates identifiers and stored integrity.
        saved = await reviews.read(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            parent_sale_id=parent_sale_id,
            operation_id=operation_id,
        )
        with session.no_autoflush:
            if not await self.repository.is_read_committed():
                # A competing return inserts children without updating the parent.
                # A repeatable snapshot can therefore remain stale after the lock.
                raise BusinessRuleError("Refund revalidation requires READ COMMITTED")
            journal = PhysicalReturnJournalRepository(session)
            parent = await journal.lock_sale(tenant_id, parent_sale_id)
            if parent is None:
                raise NotFoundError("Original electronic refund sale not found")
            if (
                parent.tenant_id != tenant_id
                or parent.id != parent_sale_id
                or parent.branch_id != saved.preview.branch_id
                or parent.status != "completed"
                or parent.sale_type != "sale"
                or parent.parent_sale_id is not None
                or parent.is_test
                or parent.currency != "TJS"
            ):
                raise ConflictError("Saved refund review source differs")
            # Lock acquisition may have waited. Recheck authority after it, and
            # read the entire history in a NEW statement under READ COMMITTED.
            await journal.assert_actor(tenant_id, actor_id, parent.branch_id)
            current = await UnitElectronicRefundPreviewService(
                UnitElectronicRefundPreviewRepository(session)
            ).quote(
                tenant_id=tenant_id,
                parent_sale_id=parent_sale_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                request=saved.request.selection,
            )
            if current != saved.preview:
                raise ConflictError("Refund history changed; review the return again")
            await journal.assert_actor(tenant_id, actor_id, parent.branch_id)
            return saved
