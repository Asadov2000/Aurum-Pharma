"""Prepare a source-bound retail event without reserving or publishing a stream.

An externally supplied position is candidate metadata, not a verified reservation.
The returned anchor is newly computed; it is not an independently persisted trust
anchor or proof that a checkout, position or event transaction committed.
"""

from dataclasses import dataclass
from uuid import UUID

from app.core.errors import ConflictError
from app.domains.pos.retail_checkout_origin_repository import RetailCheckoutOriginRepository
from app.domains.pos.retail_checkout_origin_service import RetailCheckoutOriginService
from app.domains.pos.retail_sale_event_storage import (
    RetailSaleEventAnchor,
    build_retail_sale_event_anchor,
    encode_retail_sale_event,
)
from app.domains.pos.retail_sale_events import RetailSaleStreamPosition, build_retail_sale_event


@dataclass(frozen=True, slots=True)
class RetailCheckoutEventCandidate:
    """Bounded bytes plus computed metadata; no mutable envelope is exposed."""

    anchor: RetailSaleEventAnchor
    record: bytes


class RetailCheckoutEventService:
    def __init__(self, repository: RetailCheckoutOriginRepository) -> None:
        self.repository = repository

    async def prepare(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID] | None,
        position: RetailSaleStreamPosition,
    ) -> RetailCheckoutEventCandidate:
        """Bind a supplied position to a real source's captured original writer.

        Source authorization precedes candidate parsing. Missing historical
        provenance cannot be replaced with the current writer. Matching writer
        metadata does not authorize reservation or publication in that epoch.
        """
        with self.repository.session.no_autoflush:
            evidence = await RetailCheckoutOriginService(self.repository).read_evidence(
                tenant_id=tenant_id,
                sale_id=sale_id,
                operation_id=operation_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            if evidence.origin is None:
                raise ConflictError("Retail event preparation requires a captured checkout origin")
            position = RetailSaleStreamPosition.model_validate(position.model_dump(mode="python"))
            if (
                position.origin_node_id != evidence.origin.origin_node_id
                or position.writer_epoch != evidence.origin.writer_epoch
            ):
                raise ConflictError(
                    "Retail event position differs from the captured checkout writer"
                )
            envelope = build_retail_sale_event(evidence.result, position=position)
            anchor = build_retail_sale_event_anchor(
                envelope,
                previous_source_checksum=position.previous_source_checksum,
                previous_projection_checksum=position.previous_projection_checksum,
            )
            return RetailCheckoutEventCandidate(
                anchor=anchor,
                record=encode_retail_sale_event(envelope, anchor=anchor),
            )
