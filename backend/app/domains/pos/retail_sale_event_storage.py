"""Bounded retail event representation without storage or live sync activation.

The caller must authorize access and supply an independently trusted anchor.
Neither a record nor an anchor built from that same untrusted record proves
authorization or a committed payment, stock movement or receipt transaction.
"""

import json
from typing import Literal
from uuid import UUID

from pydantic import field_validator

from app.domains.pos.retail_sale_events import (
    RetailCheckoutResult,
    RetailSaleStreamPosition,
    build_retail_sale_event,
    retail_sale_event_fingerprint,
    validate_retail_sale_event,
)
from app.domains.pos.unit_contracts import FrozenContract
from app.domains.pos.unit_event_storage import _reject_nonfinite, _unique_object
from app.domains.pos.unit_events import StreamChecksum, StreamInteger
from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.schemas import SyncEventEnvelope

MAX_RECORD_BYTES = 1024 * 1024


class RetailSaleEventAnchor(FrozenContract):
    """Evidence supplied independently from protected source/record metadata.

    The future repository owns that trust boundary. This is not a signature,
    and reconstructing an anchor from the same blob does not authenticate it.
    """

    tenant_id: UUID
    branch_id: UUID
    origin_node_id: UUID
    writer_epoch: StreamInteger
    sequence: StreamInteger
    previous_source_checksum: StreamChecksum
    previous_projection_checksum: StreamChecksum
    event_fingerprint: StreamChecksum


class _StoredRetailSaleEvent(FrozenContract):
    record_kind: Literal["retail_sale_event"]
    storage_version: Literal[1]
    envelope: dict[str, object]
    previous_source_checksum: StreamChecksum
    previous_projection_checksum: StreamChecksum
    event_fingerprint: StreamChecksum

    @field_validator("storage_version", mode="before")
    @classmethod
    def strict_storage_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("storage version must be an integer")
        return value


def build_retail_sale_event_anchor(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> RetailSaleEventAnchor:
    """Build metadata for a trusted producer to protect separately from the blob."""
    fingerprint = retail_sale_event_fingerprint(
        envelope,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )
    return RetailSaleEventAnchor(
        tenant_id=envelope.tenant_id,
        branch_id=envelope.branch_id,
        origin_node_id=envelope.origin_node_id,
        writer_epoch=envelope.writer_epoch,
        sequence=envelope.sequence,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
        event_fingerprint=fingerprint,
    )


def _anchored_result(
    envelope: SyncEventEnvelope, anchor: RetailSaleEventAnchor
) -> RetailCheckoutResult:
    if (
        envelope.tenant_id != anchor.tenant_id
        or envelope.branch_id != anchor.branch_id
        or envelope.origin_node_id != anchor.origin_node_id
        or envelope.writer_epoch != anchor.writer_epoch
        or envelope.sequence != anchor.sequence
    ):
        raise ValueError("stored retail event scope differs from its trusted anchor")
    fingerprint = retail_sale_event_fingerprint(
        envelope,
        previous_source_checksum=anchor.previous_source_checksum,
        previous_projection_checksum=anchor.previous_projection_checksum,
    )
    if fingerprint != anchor.event_fingerprint:
        raise ValueError("stored retail event differs from its trusted fingerprint")
    return validate_retail_sale_event(
        envelope,
        previous_source_checksum=anchor.previous_source_checksum,
        previous_projection_checksum=anchor.previous_projection_checksum,
    )


def encode_retail_sale_event(
    envelope: SyncEventEnvelope, *, anchor: RetailSaleEventAnchor
) -> bytes:
    """Encode bounded canonical UTF-8; do not persist, publish or apply an event."""
    anchor = RetailSaleEventAnchor.model_validate(anchor.model_dump(mode="python"))
    result = _anchored_result(envelope, anchor)
    canonical_event = build_retail_sale_event(
        result,
        position=RetailSaleStreamPosition(
            origin_node_id=anchor.origin_node_id,
            writer_epoch=anchor.writer_epoch,
            sequence=anchor.sequence,
            previous_source_checksum=anchor.previous_source_checksum,
            previous_projection_checksum=anchor.previous_projection_checksum,
        ),
    )
    record = _StoredRetailSaleEvent(
        record_kind="retail_sale_event",
        storage_version=1,
        envelope=canonical_event.model_dump(mode="json"),
        previous_source_checksum=anchor.previous_source_checksum,
        previous_projection_checksum=anchor.previous_projection_checksum,
        event_fingerprint=anchor.event_fingerprint,
    )
    encoded = canonical_json_bytes(record.model_dump(mode="json"))
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError("stored retail event exceeds the record size limit")
    return encoded


def decode_retail_sale_event(
    record: bytes, *, anchor: RetailSaleEventAnchor
) -> RetailCheckoutResult:
    """Restore validated retail facts relative to independently trusted evidence.

    Outer JSON whitespace/key order need not be canonical. Inner retail values,
    stream integrity and the fingerprint still must match the external anchor.
    """
    anchor = RetailSaleEventAnchor.model_validate(anchor.model_dump(mode="python"))
    if type(record) is not bytes or not 0 < len(record) <= MAX_RECORD_BYTES:
        raise ValueError("stored retail event must be bounded nonempty UTF-8 bytes")
    try:
        payload = json.loads(
            record.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except RecursionError as error:
        raise ValueError("stored retail event JSON is too deeply nested") from error
    stored = _StoredRetailSaleEvent.model_validate(payload)
    if (
        stored.previous_source_checksum != anchor.previous_source_checksum
        or stored.previous_projection_checksum != anchor.previous_projection_checksum
        or stored.event_fingerprint != anchor.event_fingerprint
    ):
        raise ValueError("stored retail event metadata differs from its trusted anchor")
    # JSON mode accepts UUID/time strings without coercing numeric stream metadata.
    envelope = SyncEventEnvelope.model_validate_json(
        canonical_json_bytes(stored.envelope), strict=True
    )
    return _anchored_result(envelope, anchor)
