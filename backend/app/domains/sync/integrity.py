"""Canonical hashes for sync payloads, event streams, and Edge projections."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

ZERO_CHECKSUM = "0" * 64
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize JSON without whitespace or key-order ambiguity."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_hash(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def require_checksum(value: str, *, field: str) -> str:
    if _CHECKSUM_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Sync timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def source_stream_checksum(
    *,
    previous_checksum: str,
    event_id: UUID,
    tenant_id: UUID,
    branch_id: UUID,
    origin_node_id: UUID,
    writer_epoch: int,
    sequence: int,
    operation_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    schema_version: int,
    occurred_at: datetime,
    payload_hash: str,
) -> str:
    """Bind every immutable envelope field into a rollback-safe hash chain."""
    require_checksum(previous_checksum, field="previous_checksum")
    require_checksum(payload_hash, field="payload_hash")
    return canonical_json_hash(
        {
            "previous_checksum": previous_checksum,
            "event_id": str(event_id),
            "tenant_id": str(tenant_id),
            "branch_id": str(branch_id),
            "origin_node_id": str(origin_node_id),
            "writer_epoch": writer_epoch,
            "sequence": sequence,
            "operation_id": str(operation_id),
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id),
            "event_type": event_type,
            "schema_version": schema_version,
            "occurred_at": _utc_timestamp(occurred_at),
            "payload_hash": payload_hash,
        }
    )


def sale_projection_hash(payload: Mapping[str, object]) -> str:
    """Hash the normalized sale projection reconstructed on both sides."""
    return canonical_json_hash(dict(payload))


def projection_stream_checksum(
    *,
    previous_checksum: str,
    origin_node_id: UUID,
    writer_epoch: int,
    sequence: int,
    sale_id: UUID,
    projection_hash: str,
) -> str:
    require_checksum(previous_checksum, field="previous_projection_checksum")
    require_checksum(projection_hash, field="projection_hash")
    return canonical_json_hash(
        {
            "previous_checksum": previous_checksum,
            "origin_node_id": str(origin_node_id),
            "writer_epoch": writer_epoch,
            "sequence": sequence,
            "sale_id": str(sale_id),
            "projection_hash": projection_hash,
        }
    )
