"""Read existing completed checkout evidence without constructing Edge authority.

This reader sees the caller's transaction. It neither proves a durable commit
nor assigns a historical writer, epoch, stream position or trusted event anchor.
"""

from uuid import UUID

from pydantic import ValidationError

from app.core.errors import AurumError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_cash_checkout_schemas import MixedCashOperationCompleted
from app.domains.pos.mixed_cash_checkout_status_repository import MixedCashCheckoutStatusRepository
from app.domains.pos.mixed_cash_checkout_status_service import MixedCashCheckoutStatusService
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_electronic_checkout_repository import MixedElectronicCheckoutRepository
from app.domains.pos.mixed_electronic_checkout_service import MixedElectronicCheckoutService
from app.domains.pos.physical_cash_checkout_schemas import PhysicalCashOperationCompleted
from app.domains.pos.physical_cash_checkout_status_repository import (
    PhysicalCashCheckoutStatusRepository,
)
from app.domains.pos.physical_cash_checkout_status_service import PhysicalCashCheckoutStatusService
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.retail_checkout_source_repository import (
    RetailCheckoutMarker,
    RetailCheckoutSourceRepository,
    RetailCheckoutSourceSnapshot,
)
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_electronic_checkout_repository import UnitElectronicCheckoutRepository
from app.domains.pos.unit_electronic_checkout_service import UnitElectronicCheckoutService


class RetailCheckoutSourceService:
    def __init__(self, repository: RetailCheckoutSourceRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> RetailCheckoutResult:
        session = self.repository.session
        # Existing recovery readers may refresh ORM rows. Refuse pending caller
        # changes before any SELECT rather than silently overwriting or flushing.
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Retail source reading requires a clean session")
        with session.no_autoflush:
            cart = UnitCartRepository(session)
            await cart.assert_actor(tenant_id, actor_id)
            source = await self.repository.read(tenant_id, sale_id, operation_id)
            if source is None or (source.tenant_id, source.sale_id) != (tenant_id, sale_id):
                raise NotFoundError("Retail checkout source not found")
            if source.cashier_id != actor_id or (
                allowed_branch_ids is not None and source.branch_id not in allowed_branch_ids
            ):
                raise PermissionDeniedError("Retail source branch or cashier access denied")
            await cart.assert_actor(tenant_id, actor_id, source.branch_id)
            marker = self._marker(source, operation_id)
            result = await self._read_completed(
                marker,
                tenant_id=tenant_id,
                sale_id=sale_id,
                operation_id=operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            checked = self._checked_result(result, source=source, marker=marker)
            await cart.assert_actor(tenant_id, actor_id, source.branch_id)
            return checked

    @staticmethod
    def _marker(source: RetailCheckoutSourceSnapshot, operation_id: UUID) -> RetailCheckoutMarker:
        if (
            source.status != "completed"
            or source.is_test is not False
            or source.sale_type != "sale"
            or source.parent_sale_id is not None
            or source.currency != "TJS"
        ):
            raise AurumError("Retail source must be a completed real original TJS sale")
        if source.physical is not None and source.mixed is not None:
            raise AurumError("Retail source has conflicting checkout journals")
        marker = source.physical if source.physical is not None else source.mixed
        if marker is None or marker.operation_id != operation_id:
            raise NotFoundError("Retail checkout source operation not found")
        expected_kind = "physical" if source.physical is not None else "mixed"
        if marker.kind != expected_kind or type(marker.payment_journal_version) is not int:
            raise AurumError("Retail source journal metadata is inconsistent")
        if marker.payment_journal_version in (1, 2):
            compatible = (
                marker.cash_intent_id == operation_id and marker.payment_plan_intent_id is None
            )
        elif marker.payment_journal_version == 3:
            compatible = (
                marker.payment_plan_intent_id == operation_id and marker.cash_intent_id is None
            )
        else:
            compatible = False
        if not compatible:
            raise AurumError("Retail source journal version or intent is incompatible")
        return marker

    async def _read_completed(
        self,
        marker: RetailCheckoutMarker,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> RetailCheckoutResult:
        session = self.repository.session
        if marker.kind == "physical":
            if marker.payment_journal_version == 3:
                return await UnitElectronicCheckoutService(
                    UnitElectronicCheckoutRepository(session)
                ).read(
                    tenant_id=tenant_id,
                    sale_id=sale_id,
                    operation_id=operation_id,
                    actor_id=actor_id,
                    allowed_branch_ids=allowed_branch_ids,
                )
            physical = await PhysicalCashCheckoutStatusService(
                PhysicalCashCheckoutStatusRepository(session)
            ).read(
                tenant_id=tenant_id,
                sale_id=sale_id,
                operation_id=operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            if not isinstance(physical, PhysicalCashOperationCompleted):
                raise AurumError("Retail source physical checkout is not completed")
            return physical.result
        if marker.payment_journal_version == 3:
            return await MixedElectronicCheckoutService(
                MixedElectronicCheckoutRepository(session)
            ).read(
                tenant_id=tenant_id,
                sale_id=sale_id,
                operation_id=operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
        mixed = await MixedCashCheckoutStatusService(
            MixedCashCheckoutStatusRepository(session)
        ).read(
            tenant_id=tenant_id,
            sale_id=sale_id,
            operation_id=operation_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        if not isinstance(mixed, MixedCashOperationCompleted):
            raise AurumError("Retail source mixed checkout is not completed")
        return mixed.result

    @staticmethod
    def _checked_result(
        result: RetailCheckoutResult,
        *,
        source: RetailCheckoutSourceSnapshot,
        marker: RetailCheckoutMarker,
    ) -> RetailCheckoutResult:
        try:
            if marker.kind == "physical" and isinstance(result, PhysicalCheckoutResultV3):
                checked: RetailCheckoutResult = PhysicalCheckoutResultV3.model_validate(
                    result.model_dump(mode="python")
                )
            elif marker.kind == "mixed" and isinstance(result, MixedCheckoutResultV4):
                checked = MixedCheckoutResultV4.model_validate(result.model_dump(mode="python"))
            else:
                raise AurumError("Retail source returned an incompatible checkout result")
        except ValidationError as error:
            raise AurumError("Retail source returned an invalid checkout result") from error
        if (
            checked.tenant_id != source.tenant_id
            or checked.sale_id != source.sale_id
            or checked.branch_id != source.branch_id
            or checked.cashier_id != source.cashier_id
            or checked.operation_id != marker.operation_id
            or checked.event_id != marker.operation_id
            or checked.stock_command_id != marker.operation_id
            or checked.is_test is not False
        ):
            raise AurumError("Retail source checkout result differs from its journal")
        return checked
