"""Pure v2 event integrity; deliberately absent from the live sync registry."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator

from app.domains.pos.unit_contracts import CheckoutResultV2, FrozenContract
from app.domains.sync.integrity import (
    canonical_json_bytes,
    canonical_json_hash,
    projection_stream_checksum,
    sale_projection_hash,
    source_stream_checksum,
)
from app.domains.sync.schemas import SyncEventEnvelope

StreamInteger = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
StreamChecksum = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class UnitSaleStreamPositionV2(FrozenContract):
    """A trusted caller's reserved position, not permission to publish an event."""

    origin_node_id: UUID
    writer_epoch: StreamInteger
    sequence: StreamInteger
    previous_source_checksum: StreamChecksum
    previous_projection_checksum: StreamChecksum


class _UnitSaleEnvelopeV2(SyncEventEnvelope):
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
    )

    aggregate_type: Literal["sale"] = "sale"
    event_type: Literal["pos.sale.completed.v2"] = "pos.sale.completed.v2"
    schema_version: Literal[2] = 2
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


def _result(result: CheckoutResultV2) -> CheckoutResultV2:
    # Rebuild nested contracts even when a caller used model_construct/copy.
    result = CheckoutResultV2.model_validate(result.model_dump(mode="python"))
    if tuple(line.position for line in result.lines) != tuple(range(1, len(result.lines) + 1)):
        raise ValueError("event lines must have contiguous, ordered positions")
    return result


def build_unit_sale_event(
    result: CheckoutResultV2, *, position: UnitSaleStreamPositionV2
) -> SyncEventEnvelope:
    """Build retail-only evidence; no DB writes, payment or stream reservation.

    A future transaction must prove that the result was actually committed.
    This builder cannot establish stock, payment or receipt authenticity.
    """
    result = _result(result)
    position = UnitSaleStreamPositionV2.model_validate(position.model_dump(mode="python"))
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
        event_type="pos.sale.completed.v2",
        schema_version=2,
        occurred_at=result.completed_at,
        payload_hash=payload_digest,
    )
    projection_digest_chain = projection_stream_checksum(
        previous_checksum=position.previous_projection_checksum,
        origin_node_id=position.origin_node_id,
        writer_epoch=position.writer_epoch,
        sequence=position.sequence,
        sale_id=result.sale_id,
        projection_hash=projection_digest,
    )
    return _UnitSaleEnvelopeV2(
        event_id=result.event_id,
        tenant_id=result.tenant_id,
        branch_id=result.branch_id,
        origin_node_id=position.origin_node_id,
        writer_epoch=position.writer_epoch,
        sequence=position.sequence,
        operation_id=result.operation_id,
        aggregate_type="sale",
        aggregate_id=result.sale_id,
        event_type="pos.sale.completed.v2",
        schema_version=2,
        occurred_at=result.completed_at,
        payload=payload,
        payload_hash=payload_digest,
        stream_checksum=source_digest,
        projection_hash=projection_digest,
        projection_checksum=projection_digest_chain,
    )


def _checked_event(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> tuple[_UnitSaleEnvelopeV2, CheckoutResultV2]:
    event = _UnitSaleEnvelopeV2.model_validate(envelope.model_dump(mode="python"))
    if canonical_json_hash(event.payload) != event.payload_hash:
        raise ValueError("event payload hash mismatch")
    result = _result(CheckoutResultV2.model_validate(event.payload))
    payload = result.model_dump(mode="json")
    if canonical_json_bytes(event.payload) != canonical_json_bytes(payload):
        raise ValueError("event payload must use canonical v2 retail serialization")
    if (
        result.event_id != event.event_id
        or result.tenant_id != event.tenant_id
        or result.branch_id != event.branch_id
        or result.sale_id != event.aggregate_id
        or result.operation_id != event.operation_id
        or result.completed_at != event.occurred_at
    ):
        raise ValueError("event context differs from its result")
    position = UnitSaleStreamPositionV2(
        origin_node_id=event.origin_node_id,
        writer_epoch=event.writer_epoch,
        sequence=event.sequence,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )
    expected = build_unit_sale_event(result, position=position)
    if event.stream_checksum != expected.stream_checksum:
        raise ValueError("event source checksum mismatch")
    if event.projection_hash != expected.projection_hash:
        raise ValueError("event projection hash mismatch")
    if event.projection_checksum != expected.projection_checksum:
        raise ValueError("event projection checksum mismatch")
    return event, result


def validate_unit_sale_event(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> CheckoutResultV2:
    """Validate integrity only; hashes neither authorize nor apply a checkout."""
    return _checked_event(
        envelope,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )[1]


def unit_sale_event_fingerprint(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> str:
    """Replay identity of validated immutable evidence, never a request hash."""
    event, _ = _checked_event(
        envelope,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )
    return canonical_json_hash(
        {"domain": "aurum-pos-unit-sale-event-v2", "envelope": event.model_dump(mode="json")}
    )
