"""Exclusive durable refund preparation; terminal actions remain unavailable."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_electronic_refund_binding_models import (
    UnitElectronicRefundBinding,
    UnitElectronicRefundBindingCancel,
)
from app.domains.pos.unit_electronic_refund_binding_repository import (
    UnitElectronicRefundBindingRepository,
)
from app.domains.pos.unit_electronic_refund_binding_schemas import UnitElectronicRefundBindingRead
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import SavedElectronicRefundIntent
from app.domains.pos.unit_electronic_refund_intent_service import UnitElectronicRefundIntentService
from app.domains.pos.unit_electronic_refund_revalidation import (
    UnitElectronicRefundRevalidationService,
)


class UnitElectronicRefundBindingService:
    def __init__(self, repository: UnitElectronicRefundBindingRepository) -> None:
        self.repository = repository
        self.reviews = UnitElectronicRefundIntentService(
            UnitElectronicRefundIntentRepository(repository.session)
        )

    def _clean_session(self) -> None:
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending changes before changing refund preparation")

    async def bind(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
    ) -> UnitElectronicRefundBindingRead:
        """Persist one exclusive preparation before a future terminal workflow.

        Caller owns commit. This is not a payout attempt or payment evidence;
        future execution must revalidate scope, write policy, shift and register.
        """
        self._clean_session()
        # Validate identifiers/authority before acquiring any operation lock.
        saved = await self.reviews.read(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            parent_sale_id=parent_sale_id,
            operation_id=operation_id,
        )
        try:
            async with self.repository.session.begin_nested():
                await self.repository.lock_operation(tenant_id, operation_id)
                binding, cancellation = await self.repository.get_state(tenant_id, operation_id)
                if cancellation is not None:
                    raise ConflictError("Electronic refund preparation was permanently cancelled")
                if binding is None:
                    saved = await UnitElectronicRefundRevalidationService(
                        UnitElectronicRefundIntentRepository(self.repository.session)
                    ).revalidate_locked_review(
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        allowed_branch_ids=allowed_branch_ids,
                        parent_sale_id=parent_sale_id,
                        operation_id=operation_id,
                    )
                    binding = await self.repository.claim_binding(
                        UnitElectronicRefundBinding(
                            tenant_id=tenant_id,
                            id=operation_id,
                            parent_sale_id=parent_sale_id,
                            branch_id=saved.preview.branch_id,
                            actor_id=actor_id,
                            created_by=actor_id,
                        )
                    )
                await PhysicalReturnJournalRepository(self.repository.session).assert_actor(
                    tenant_id, actor_id, saved.preview.branch_id
                )
                return self._result(saved, binding, None, tenant_id, actor_id)
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == "23505":
                raise ConflictError(
                    "Original sale already has an active refund preparation"
                ) from None
            raise

    async def cancel(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
    ) -> UnitElectronicRefundBindingRead:
        """Permanent cancellation also wins against a delayed first binding."""
        self._clean_session()
        saved = await self.reviews.read(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            parent_sale_id=parent_sale_id,
            operation_id=operation_id,
        )
        async with self.repository.session.begin_nested():
            await self.repository.lock_operation(tenant_id, operation_id)
            binding, cancellation = await self.repository.get_state(tenant_id, operation_id)
            if cancellation is None:
                if not await self.repository.is_read_committed():
                    raise BusinessRuleError("Refund preparation requires READ COMMITTED")
                journal = PhysicalReturnJournalRepository(self.repository.session)
                parent = await journal.lock_sale(tenant_id, parent_sale_id)
                if parent is None:
                    raise NotFoundError("Original electronic refund sale not found")
                if (parent.tenant_id, parent.id, parent.branch_id) != (
                    tenant_id,
                    parent_sale_id,
                    saved.preview.branch_id,
                ):
                    raise ConflictError("Saved refund review source differs")
                await journal.assert_actor(tenant_id, actor_id, parent.branch_id)
                cancellation = await self.repository.claim_cancel(
                    UnitElectronicRefundBindingCancel(
                        tenant_id=tenant_id,
                        id=operation_id,
                        parent_sale_id=parent_sale_id,
                        branch_id=saved.preview.branch_id,
                        actor_id=actor_id,
                        created_by=actor_id,
                    )
                )
            await PhysicalReturnJournalRepository(self.repository.session).assert_actor(
                tenant_id, actor_id, saved.preview.branch_id
            )
            return self._result(saved, binding, cancellation, tenant_id, actor_id)

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
    ) -> UnitElectronicRefundBindingRead:
        with self.repository.session.no_autoflush:
            saved = await self.reviews.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                parent_sale_id=parent_sale_id,
                operation_id=operation_id,
            )
            binding, cancellation = await self.repository.get_state(tenant_id, operation_id)
            return self._result(saved, binding, cancellation, tenant_id, actor_id)

    @staticmethod
    def _result(
        saved: SavedElectronicRefundIntent,
        binding: UnitElectronicRefundBinding | None,
        cancellation: UnitElectronicRefundBindingCancel | None,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> UnitElectronicRefundBindingRead:
        if binding is None and cancellation is None:
            raise NotFoundError("Electronic refund preparation not found")
        try:
            for record in (binding, cancellation):
                if record is not None and (
                    record.tenant_id,
                    record.id,
                    record.parent_sale_id,
                    record.branch_id,
                    record.actor_id,
                    record.created_by,
                ) != (
                    tenant_id,
                    saved.operation_id,
                    saved.preview.parent_sale_id,
                    saved.preview.branch_id,
                    actor_id,
                    actor_id,
                ):
                    raise ValueError("Stored preparation scope differs")
            return UnitElectronicRefundBindingRead(
                state="cancelled" if cancellation is not None else "bound",
                operation_id=saved.operation_id,
                parent_sale_id=saved.preview.parent_sale_id,
                review_revision_hash=saved.preview.revision_hash,
                bound_at=binding.created_at if binding is not None else None,
                cancelled_at=cancellation.created_at if cancellation is not None else None,
            )
        except (ValidationError, ValueError):
            raise AurumError("Stored electronic refund preparation is inconsistent") from None
