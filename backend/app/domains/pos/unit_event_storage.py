"""Versioned v2 persistence representation, without a database or sync activation."""

import json
from typing import Literal, NoReturn
from uuid import UUID

from pydantic import field_validator

from app.domains.pos.unit_contracts import CheckoutResultV2, FrozenContract
from app.domains.pos.unit_events import (
    StreamChecksum,
    StreamInteger,
    UnitSaleStreamPositionV2,
    build_unit_sale_event,
    unit_sale_event_fingerprint,
    validate_unit_sale_event,
)
from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.schemas import SyncEventEnvelope

MAX_RECORD_BYTES = 1024 * 1024


class UnitSaleEventAnchorV2(FrozenContract):
    """Independent trusted evidence, never something recovered from the same blob.

    The future repository must obtain this from its protected source identity
    and immutable record metadata. It is neither a signature nor authorization.
    """

    tenant_id: UUID
    branch_id: UUID
    origin_node_id: UUID
    writer_epoch: StreamInteger
    sequence: StreamInteger
    previous_source_checksum: StreamChecksum
    previous_projection_checksum: StreamChecksum
    event_fingerprint: StreamChecksum


class _StoredUnitSaleEventV2(FrozenContract):
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


def build_unit_sale_event_anchor(
    envelope: SyncEventEnvelope,
    *,
    previous_source_checksum: str,
    previous_projection_checksum: str,
) -> UnitSaleEventAnchorV2:
    """Build evidence for a trusted producer to store separately from the bytes."""
    fingerprint = unit_sale_event_fingerprint(
        envelope,
        previous_source_checksum=previous_source_checksum,
        previous_projection_checksum=previous_projection_checksum,
    )
    return UnitSaleEventAnchorV2(
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
    envelope: SyncEventEnvelope, anchor: UnitSaleEventAnchorV2
) -> CheckoutResultV2:
    if (
        envelope.tenant_id != anchor.tenant_id
        or envelope.branch_id != anchor.branch_id
        or envelope.origin_node_id != anchor.origin_node_id
        or envelope.writer_epoch != anchor.writer_epoch
        or envelope.sequence != anchor.sequence
    ):
        raise ValueError("stored event scope differs from its trusted anchor")
    fingerprint = unit_sale_event_fingerprint(
        envelope,
        previous_source_checksum=anchor.previous_source_checksum,
        previous_projection_checksum=anchor.previous_projection_checksum,
    )
    if fingerprint != anchor.event_fingerprint:
        raise ValueError("stored event differs from its trusted fingerprint")
    return validate_unit_sale_event(
        envelope,
        previous_source_checksum=anchor.previous_source_checksum,
        previous_projection_checksum=anchor.previous_projection_checksum,
    )


def encode_unit_sale_event(envelope: SyncEventEnvelope, *, anchor: UnitSaleEventAnchorV2) -> bytes:
    """Produce bounded canonical UTF-8; no persistence or payment side effects."""
    anchor = UnitSaleEventAnchorV2.model_validate(anchor.model_dump(mode="python"))
    result = _anchored_result(envelope, anchor)
    canonical_event = build_unit_sale_event(
        result,
        position=UnitSaleStreamPositionV2(
            origin_node_id=anchor.origin_node_id,
            writer_epoch=anchor.writer_epoch,
            sequence=anchor.sequence,
            previous_source_checksum=anchor.previous_source_checksum,
            previous_projection_checksum=anchor.previous_projection_checksum,
        ),
    )
    record = _StoredUnitSaleEventV2(
        storage_version=1,
        envelope=canonical_event.model_dump(mode="json"),
        previous_source_checksum=anchor.previous_source_checksum,
        previous_projection_checksum=anchor.previous_projection_checksum,
        event_fingerprint=anchor.event_fingerprint,
    )
    encoded = canonical_json_bytes(record.model_dump(mode="json"))
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError("stored event exceeds the record size limit")
    return encoded


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("stored event contains duplicate JSON fields")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> NoReturn:
    raise ValueError("stored event contains a non-finite JSON number")


def decode_unit_sale_event(record: bytes, *, anchor: UnitSaleEventAnchorV2) -> CheckoutResultV2:
    """Restore retail facts only, comparing the blob to independent evidence.

    The caller must authorize access before supplying the anchor. A valid
    representation does not prove committed cash, stock or receipt facts.
    """
    anchor = UnitSaleEventAnchorV2.model_validate(anchor.model_dump(mode="python"))
    if type(record) is not bytes or not 0 < len(record) <= MAX_RECORD_BYTES:
        raise ValueError("stored event must be bounded nonempty UTF-8 bytes")
    try:
        payload = json.loads(
            record.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except RecursionError as error:
        raise ValueError("stored event JSON is too deeply nested") from error
    stored = _StoredUnitSaleEventV2.model_validate(payload)
    if (
        stored.previous_source_checksum != anchor.previous_source_checksum
        or stored.previous_projection_checksum != anchor.previous_projection_checksum
        or stored.event_fingerprint != anchor.event_fingerprint
    ):
        raise ValueError("stored event metadata differs from its trusted anchor")
    # Strict JSON mode accepts wire UUID/time strings, but never coerces
    # bool/string/float versions, writer epochs or sequences into integers.
    envelope = SyncEventEnvelope.model_validate_json(
        canonical_json_bytes(stored.envelope), strict=True
    )
    return _anchored_result(envelope, anchor)
