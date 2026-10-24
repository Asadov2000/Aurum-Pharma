"""Save an irreversible activation before future external reconciliation."""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.domains.pos.mixed_electronic_refund_activation_models import (
    MixedElectronicRefundActivation,
)
from app.domains.pos.mixed_electronic_refund_activation_repository import (
    MixedElectronicRefundActivationRepository,
)
from app.domains.pos.mixed_electronic_refund_activation_schemas import (
    SavedMixedElectronicRefundActivation,
)
from app.domains.pos.mixed_electronic_refund_binding_repository import (
    MixedElectronicRefundBindingRepository,
)
from app.domains.pos.mixed_electronic_refund_binding_service import (
    MixedElectronicRefundBindingService,
)
from app.domains.pos.mixed_electronic_refund_intent_repository import (
    MixedElectronicRefundIntentRepository,
)
from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SavedMixedElectronicRefundIntent,
)
from app.domains.pos.mixed_electronic_refund_intent_service import (
    MixedElectronicRefundIntentService,
)
from app.domains.pos.mixed_electronic_refund_revalidation import (
    MixedElectronicRefundRevalidationService,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository


class MixedElectronicRefundActivationService:
    def __init__(self, repository: MixedElectronicRefundActivationRepository) -> None:
        self.repository = repository
        session = repository.session
        reviews = MixedElectronicRefundIntentRepository(session)
        self.reviews = MixedElectronicRefundIntentService(reviews)
        self.revalidation = MixedElectronicRefundRevalidationService(reviews)
        self.bindings = MixedElectronicRefundBindingRepository(session)
        self.authority = MixedReturnJournalRepository(session)
        self.registers = PhysicalCashRefundRepository(session)

    async def start(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
        shift_id: UUID,
    ) -> SavedMixedElectronicRefundActivation:
        """Caller must commit before any future external action, outside this lock.

        A lost-response retry restores the marker, never repeats an external
        action. No terminal or payout consumer is enabled by this service.
        """
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending changes before activating a refund")
        if not isinstance(shift_id, UUID):
            raise BusinessRuleError("Refund activation shift must use UUID")
        saved = await self.reviews.read(
            tenant_id=tenant_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            parent_sale_id=parent_sale_id,
            operation_id=operation_id,
        )
        async with session.begin_nested():
            await self.bindings.lock_operation(tenant_id, operation_id)
            marker = await self.repository.get(tenant_id, operation_id)
            if marker is not None:
                if marker.shift_id != shift_id:
                    raise ConflictError("Refund activation was saved for another shift")
            else:
                saved = await self.revalidation.revalidate_locked_review(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    allowed_branch_ids=allowed_branch_ids,
                    parent_sale_id=parent_sale_id,
                    operation_id=operation_id,
                )
                binding, cancellation = await self.bindings.get_state(tenant_id, operation_id)
                if binding is None or cancellation is not None:
                    raise ConflictError("Refund activation requires its active preparation")
                # Validate the persisted binding scope as well as its existence.
                MixedElectronicRefundBindingService._result(
                    saved, binding, cancellation, tenant_id, actor_id
                )
                parent = await self.authority.lock_sale(tenant_id, parent_sale_id)
                if parent is None:
                    raise NotFoundError("Original electronic refund sale not found")
                if (parent.tenant_id, parent.id, parent.branch_id) != (
                    tenant_id,
                    parent_sale_id,
                    saved.preview.branch_id,
                ):
                    raise ConflictError("Refund activation source scope differs")
                shift = await self.registers.lock_shift(tenant_id, shift_id)
                if shift is None or (
                    shift.tenant_id,
                    shift.id,
                    shift.status,
                    shift.branch_id,
                    shift.register_id,
                    shift.opened_by_user_id,
                    shift.currency,
                ) != (
                    tenant_id,
                    shift_id,
                    "open",
                    saved.preview.branch_id,
                    parent.register_id,
                    actor_id,
                    "TJS",
                ):
                    raise BusinessRuleError(
                        "Refund activation requires your open original-register shift"
                    )
                if not await self.registers.lock_active_register(
                    tenant_id, saved.preview.branch_id, parent.register_id
                ):
                    raise BusinessRuleError(
                        "Refund activation requires an active original register"
                    )
                await self.authority.assert_actor(tenant_id, actor_id, saved.preview.branch_id)
                marker = MixedElectronicRefundActivation(
                    tenant_id=tenant_id,
                    id=operation_id,
                    parent_sale_id=parent_sale_id,
                    branch_id=saved.preview.branch_id,
                    actor_id=actor_id,
                    register_id=parent.register_id,
                    shift_id=shift_id,
                    created_by=actor_id,
                )
                await self.repository.insert(marker)
            await self.authority.assert_actor(tenant_id, actor_id, saved.preview.branch_id)
            return self._result(marker, saved, tenant_id, actor_id)

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        parent_sale_id: UUID,
        operation_id: UUID,
    ) -> SavedMixedElectronicRefundActivation:
        with self.repository.session.no_autoflush:
            saved = await self.reviews.read(
                tenant_id=tenant_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
                parent_sale_id=parent_sale_id,
                operation_id=operation_id,
            )
            marker = await self.repository.get(tenant_id, operation_id)
            if marker is None:
                raise NotFoundError("Electronic refund activation not found")
            await self.authority.assert_actor(tenant_id, actor_id, saved.preview.branch_id)
            return self._result(marker, saved, tenant_id, actor_id)

    @staticmethod
    def _result(
        marker: MixedElectronicRefundActivation,
        saved: SavedMixedElectronicRefundIntent,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> SavedMixedElectronicRefundActivation:
        try:
            if (
                marker.tenant_id,
                marker.id,
                marker.parent_sale_id,
                marker.branch_id,
                marker.actor_id,
                marker.created_by,
            ) != (
                tenant_id,
                saved.operation_id,
                saved.preview.parent_sale_id,
                saved.preview.branch_id,
                actor_id,
                actor_id,
            ) or marker.created_at < saved.saved_at:
                raise ValueError("Stored activation scope differs")
            return SavedMixedElectronicRefundActivation(
                operation_id=marker.id,
                parent_sale_id=marker.parent_sale_id,
                branch_id=marker.branch_id,
                actor_id=marker.actor_id,
                register_id=marker.register_id,
                shift_id=marker.shift_id,
                activated_at=marker.created_at,
                review=saved,
            )
        except (ValidationError, ValueError, TypeError):
            raise AurumError("Stored electronic refund activation is inconsistent") from None
