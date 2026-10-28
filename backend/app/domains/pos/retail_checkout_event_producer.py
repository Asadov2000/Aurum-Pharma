"""Transaction-bound retail producer, dormant until an owner enables its scope.

The caller owns the checkout transaction and commit. The producer does not send
events or enable a receiver; the default-off decision is captured by PostgreSQL
with the new checkout origin, before any event position can be reserved.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, ConflictError
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.retail_checkout_event_models import RetailCheckoutEventSnapshot
from app.domains.pos.retail_checkout_event_repository import RetailCheckoutEventRepository
from app.domains.pos.retail_checkout_event_service import (
    RetailCheckoutEventCandidate,
    build_retail_checkout_event_candidate,
)
from app.domains.pos.retail_checkout_origin_repository import RetailCheckoutOriginRepository
from app.domains.pos.retail_checkout_origin_service import (
    RetailCheckoutOriginEvidence,
    RetailCheckoutOriginService,
)
from app.domains.pos.retail_sale_event_storage import (
    RetailSaleEventAnchor,
    decode_retail_sale_event,
)
from app.domains.pos.retail_sale_events import RetailCheckoutResult, RetailSaleStreamPosition
from app.domains.pos.unit_cart_repository import UnitCartRepository


class RetailCheckoutEventProducer:
    def __init__(self, repository: RetailCheckoutEventRepository) -> None:
        self.repository = repository

    async def produce(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
    ) -> RetailCheckoutEventCandidate | None:
        """Persist a new required event or read an authorized immutable replay.

        Existing events are restored before checking current transaction/writer
        state. A missing event can only be created in its captured source
        transaction; historical checkouts never acquire an invented position.
        """
        session = self.repository.session
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Retail event production requires a clean session")
        with session.no_autoflush:
            evidence = await RetailCheckoutOriginService(
                RetailCheckoutOriginRepository(session)
            ).read_evidence(
                tenant_id=tenant_id,
                sale_id=sale_id,
                operation_id=operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            saved = await self.repository.read(tenant_id, sale_id, operation_id)
            if saved is not None:
                return self._restore(saved, evidence)
            capture = await self.repository.capture_state(tenant_id, sale_id, operation_id)
            if capture is None or not capture.required:
                return None
            if not capture.current_transaction:
                raise ConflictError("A previous checkout transaction cannot acquire a new event")
            async with session.begin_nested():
                position = await self.repository.reserve(tenant_id, sale_id, operation_id)
                candidate = build_retail_checkout_event_candidate(
                    evidence,
                    position=RetailSaleStreamPosition(
                        origin_node_id=position.origin_node_id,
                        writer_epoch=position.writer_epoch,
                        sequence=position.sequence,
                        previous_source_checksum=position.previous_checksum,
                        previous_projection_checksum=position.previous_projection_checksum,
                    ),
                )
                await self.repository.store(
                    tenant_id=tenant_id,
                    sale_id=sale_id,
                    operation_id=operation_id,
                    record=candidate.record,
                    previous_source_checksum=candidate.anchor.previous_source_checksum,
                    previous_projection_checksum=candidate.anchor.previous_projection_checksum,
                    event_fingerprint=candidate.anchor.event_fingerprint,
                )
                await self.repository.validate_completion()
                await UnitCartRepository(session).assert_actor(
                    tenant_id, actor_id, evidence.result.branch_id
                )
                return candidate

    @staticmethod
    def _restore(
        saved: RetailCheckoutEventSnapshot, evidence: RetailCheckoutOriginEvidence
    ) -> RetailCheckoutEventCandidate:
        source, origin = evidence.result, evidence.origin
        kind = "physical" if isinstance(source, PhysicalCheckoutResultV3) else "mixed"
        if origin is None or (
            saved.tenant_id != source.tenant_id
            or saved.sale_id != source.sale_id
            or saved.operation_id != source.operation_id
            or saved.event_id != source.event_id
            or saved.source_kind != kind
            or saved.branch_id != source.branch_id
            or saved.register_id != source.register_id
            or saved.cashier_id != source.cashier_id
            or saved.origin_node_id != origin.origin_node_id
            or saved.writer_epoch != origin.writer_epoch
        ):
            raise AurumError("Stored retail checkout event differs from its source")
        try:
            anchor = RetailSaleEventAnchor(
                tenant_id=saved.tenant_id,
                branch_id=saved.branch_id,
                origin_node_id=saved.origin_node_id,
                writer_epoch=saved.writer_epoch,
                sequence=saved.sequence,
                previous_source_checksum=saved.previous_source_checksum,
                previous_projection_checksum=saved.previous_projection_checksum,
                event_fingerprint=saved.event_fingerprint,
            )
            restored = decode_retail_sale_event(saved.record, anchor=anchor)
        except ValueError:
            # Never expose a malformed stored checkout/receipt in error details.
            raise AurumError("Stored retail checkout event is invalid") from None
        if restored != source:
            raise AurumError("Stored retail checkout event differs from its source")
        return RetailCheckoutEventCandidate(anchor=anchor, record=saved.record)


async def produce_retail_checkout_event(
    session: AsyncSession,
    result: RetailCheckoutResult,
    *,
    actor_id: UUID,
    allowed_branch_ids: set[UUID] | None,
) -> RetailCheckoutEventCandidate | None:
    """Optional checkout hook, not an authorized financial recovery reader.

    A dormant captured decision must not introduce financial recovery checks
    into the existing checkout. A required event always uses the strict producer;
    its source IDs are read back and the caller's financial payload is not trusted.
    """
    if session.new or session.dirty or session.deleted:
        raise ConflictError("Retail event production requires a clean session")
    with session.no_autoflush:
        repository = RetailCheckoutEventRepository(session)
        capture = await repository.capture_state(
            result.tenant_id, result.sale_id, result.operation_id
        )
        if capture is None or not capture.required:
            return None
        return await RetailCheckoutEventProducer(repository).produce(
            tenant_id=result.tenant_id,
            sale_id=result.sale_id,
            operation_id=result.operation_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
        )
