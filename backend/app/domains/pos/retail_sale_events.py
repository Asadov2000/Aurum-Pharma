"""Unsigned retail event integrity, deliberately absent from the live sync registry.

Hashes neither authorize a sale nor prove its stock/payment transaction committed.
The trusted caller owns the stream position and prior checksums. A consistently
rewritten unsigned event cannot be authenticated by recomputing these hashes.
"""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, field_validator, model_validator

from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_contracts import FrozenContract
from app.domains.pos.unit_events import StreamChecksum, StreamInteger
from app.domains.sync.integrity import (
    canonical_json_bytes,
    canonical_json_hash,
    projection_stream_checksum,
    sale_projection_hash,
    source_stream_checksum,
)
from app.domains.sync.schemas import SyncEventEnvelope

RetailCheckoutResult = PhysicalCheckoutResultV3 | MixedCheckoutResultV4
RetailEventType = Literal["pos.sale.completed.v3", "pos.sale.completed.v4"]


class RetailSaleStreamPosition(FrozenContract):
    """A trusted caller's reserved position, not permission to publish an event."""

    origin_node_id: UUID
    writer_epoch: StreamInteger
    sequence: StreamInteger
    previous_source_checksum: StreamChecksum
    previous_projection_checksum: StreamChecksum


class _RetailSaleEnvelope(SyncEventEnvelope):
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
    )

    aggregate_type: Literal["sale"] = "sale"
    event_type: RetailEventType
    schema_version: Literal[3, 4]
    writer_epoch: StreamInteger
    sequence: StreamInteger
    occurred_at: AwareDatetime

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("event version must be an integer")
        return value

    @field_validator("occurred_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def matching_event_version(self) -> Self:
        expected = "pos.sale.completed.v3" if self.schema_version == 3 else "pos.sale.completed.v4"
        if self.event_type != expected:
            raise ValueError("retail event type differs from its schema version")
        return self


def _result(result: RetailCheckoutResult) -> RetailCheckoutResult:
    # Rebuild nested contracts even after model_construct or unchecked model_copy.
    if isinstance(result, PhysicalCheckoutResultV3):
        return PhysicalCheckoutResultV3.model_validate(result.model_dump(mode="python"))
    if isinstance(result, MixedCheckoutResultV4):
        return MixedCheckoutResultV4.model_validate(result.model_dump(mode="python"))
    raise ValueError("unsupported retail checkout result")


def _event_type(result: RetailCheckoutResult) -> RetailEventType:
    if isinstance(result, PhysicalCheckoutResultV3):
        return "pos.sale.completed.v3"
    return "pos.sale.completed.v4"


def build_retail_sale_event(
    result: RetailCheckoutResult, *, position: RetailSaleStreamPosition
) -> SyncEventEnvelope:
    """Build retail-only evidence without reserving a stream or applying a sale."""
    result = _result(result)
    position = RetailSaleStreamPosition.model_validate(position.model_dump(mode="python"))
    event_type = _event_type(result)
    payload = result.model_dump(mode="json")
    payload_digest = canonical_json_hash(payload)
    projection_digest = sale_projection_hash(payload)
    source_digest = source_stream_checksum(
        previous_checksum=position.previous_source_checksum,
        event_id=result.event_id,
        tenant_id=result.tenant_id,
        branch_id=result.branch_id,
        origin_node_id=position.origin_node_id,
        writer_epoch=position.writer_epoch,
        sequence=position.sequence,
        operation_id=result.operation_id,
        aggregate_type="sale",
        aggregate_id=result.sale_id,
        event_type=event_type,
        schema_version=result.schema_version,
        occurred_at=result.completed_at,
        payload_hash=payload_digest,
    )
    projection_chain_digest = projection_stream_checksum(
        previous_checksum=position.previous_projection_checksum,
        origin_node_id=position.origin_node_id,
        writer_epoch=position.writer_epoch,
        sequence=position.sequence,
        sale_id=result.sale_id,
        projection_hash=projection_digest,
    )
    return _RetailSaleEnvelope(
        event_id=result.event_id,
        tenant_id=result.tenant_id,
        branch_id=result.branch_id,
        origin_node_id=position.origin_node_id,
        writer_epoch=position.writer_epoch,
        sequence=position.sequence,
        operation_id=result.operation_id,
        aggregate_type="sale",
        aggregate_id=result.sale_id,
        event_type=event_type,
        schema_version=result.schema_version,
        occurred_at=result.completed_at,
        payload=payload,
        payload_hash=payload_digest,
        stream_checksum=source_digest,
        projection_hash=projection_digest,
        projection_checksum=projection_chain_digest,
    )


def _checked_event(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> tuple[_RetailSaleEnvelope, RetailCheckoutResult]:
    event = _RetailSaleEnvelope.model_validate(envelope.model_dump(mode="python"))
    if canonical_json_hash(event.payload) != event.payload_hash:
        raise ValueError("event payload hash mismatch")
    result: RetailCheckoutResult
    if event.schema_version == 3:
        result = PhysicalCheckoutResultV3.model_validate(event.payload)
    else:
        result = MixedCheckoutResultV4.model_validate(event.payload)
    payload = result.model_dump(mode="json")
    if canonical_json_bytes(event.payload) != canonical_json_bytes(payload):
        raise ValueError("event payload must use canonical retail serialization")
    if (
        result.event_id != event.event_id
        or result.tenant_id != event.tenant_id
        or result.branch_id != event.branch_id
        or result.sale_id != event.aggregate_id
        or result.operation_id != event.operation_id
        or result.completed_at != event.occurred_at
    ):
        raise ValueError("event context differs from its result")
    position = RetailSaleStreamPosition(
        origin_node_id=event.origin_node_id,
        writer_epoch=event.writer_epoch,
        sequence=event.sequence,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )
    expected = build_retail_sale_event(result, position=position)
    if event.stream_checksum != expected.stream_checksum:
        raise ValueError("event source checksum mismatch")
    if event.projection_hash != expected.projection_hash:
        raise ValueError("event projection hash mismatch")
    if event.projection_checksum != expected.projection_checksum:
        raise ValueError("event projection checksum mismatch")
    return event, result


def validate_retail_sale_event(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> RetailCheckoutResult:
    """Validate unsigned integrity only; do not authorize or apply a checkout."""
    return _checked_event(
        envelope,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )[1]


def retail_sale_event_fingerprint(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> str:
    """Replay identity of checked immutable evidence, never an authenticated anchor."""
    event, _ = _checked_event(
        envelope,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )
    return canonical_json_hash(
        {
            "domain": "aurum-pos-retail-sale-event",
            "schema_version": event.schema_version,
            "envelope": event.model_dump(mode="json"),
        }
    )
