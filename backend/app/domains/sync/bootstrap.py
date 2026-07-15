"""Authenticated, chunked bootstrap contract for development Edge nodes.

The MAC key is domain-separated from the one-time node credential digest. This
transport remains development-only; production still requires an asymmetric
signing key protected outside the application database and mTLS device identity.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.integrity import canonical_json_bytes, canonical_json_hash, require_checksum
from app.domains.sync.schemas import (
    SyncBootstrapChunkDescriptor,
    SyncBootstrapChunkRead,
    SyncBootstrapManifest,
    SyncBootstrapManifestRead,
    SyncEventEnvelope,
    SyncPullResponse,
)

SIGNATURE_ALGORITHM = "hmac-sha256-edge-v1"
_KEY_DOMAIN = b"aurum-edge-bootstrap-mac-v1\x00"
_BOOTSTRAP_ID_DOMAIN = "https://aurum-pharma.tj/protocol/edge-bootstrap/v1"


class BootstrapValidationError(ValueError):
    """Bootstrap cannot be trusted or does not match the expected enrollment."""


@dataclass(frozen=True, slots=True)
class BootstrapScope:
    edge_node_id: UUID
    tenant_id: UUID
    branch_id: UUID
    credential_kid: UUID
    credential_digest: str
    credential_issued_at: datetime
    credential_expires_at: datetime
    origin_node_id: UUID
    writer_epoch: int
    root_source_checksum: str
    root_projection_checksum: str
    checkpoint_sequence: int
    source_checksum: str
    projection_checksum: str


def _utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None:
        raise BootstrapValidationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def chunk_payload_hash(*, index: int, events: list[SyncEventEnvelope]) -> str:
    return canonical_json_hash(
        {
            "schema_version": 1,
            "chunk_index": index,
            "events": [event.model_dump(mode="json") for event in events],
        }
    )


def _snapshot_hash(descriptors: list[SyncBootstrapChunkDescriptor]) -> str:
    return canonical_json_hash(
        {
            "schema_version": 1,
            "chunk_hashes": [descriptor.payload_hash for descriptor in descriptors],
        }
    )


def bootstrap_id_for(
    *,
    edge_node_id: UUID,
    tenant_id: UUID,
    branch_id: UUID,
    credential_kid: UUID,
    origin_node_id: UUID,
    writer_epoch: int,
    checkpoint_sequence: int,
    issued_at: datetime,
) -> UUID:
    issued_at = _utc(issued_at, field="issued_at")
    seed = canonical_json_hash(
        {
            "domain": _BOOTSTRAP_ID_DOMAIN,
            "edge_node_id": str(edge_node_id),
            "tenant_id": str(tenant_id),
            "branch_id": str(branch_id),
            "credential_kid": str(credential_kid),
            "origin_node_id": str(origin_node_id),
            "writer_epoch": writer_epoch,
            "checkpoint_sequence": checkpoint_sequence,
            "issued_at": issued_at.astimezone(UTC).isoformat(timespec="microseconds"),
        }
    )
    return uuid5(NAMESPACE_URL, seed)


def _mac_key(credential_digest: str) -> bytes:
    require_checksum(credential_digest, field="credential_digest")
    return hmac.new(bytes.fromhex(credential_digest), _KEY_DOMAIN, hashlib.sha256).digest()


def manifest_hash(manifest: SyncBootstrapManifest) -> str:
    return canonical_json_hash(manifest.model_dump(mode="json"))


def sign_manifest(manifest: SyncBootstrapManifest, *, credential_digest: str) -> str:
    payload = canonical_json_bytes(manifest.model_dump(mode="json"))
    return hmac.new(_mac_key(credential_digest), payload, hashlib.sha256).hexdigest()


def bootstrap_expires_at(
    *,
    credential_issued_at: datetime,
    credential_expires_at: datetime,
    ttl_seconds: int,
) -> datetime:
    issued_at = _utc(credential_issued_at, field="credential_issued_at")
    credential_expires_at = _utc(credential_expires_at, field="credential_expires_at")
    expires_at = min(credential_expires_at, issued_at + timedelta(seconds=ttl_seconds))
    if expires_at <= issued_at:
        raise BootstrapValidationError("Bootstrap validity window is empty")
    return expires_at


def build_manifest(
    *,
    edge_node_id: UUID,
    tenant_id: UUID,
    branch_id: UUID,
    credential_kid: UUID,
    credential_digest: str,
    credential_issued_at: datetime,
    credential_expires_at: datetime,
    origin_node_id: UUID,
    writer_epoch: int,
    root_source_checksum: str,
    root_projection_checksum: str,
    checkpoint_sequence: int,
    source_checksum: str,
    projection_checksum: str,
    events: list[SyncEventEnvelope],
    chunk_size: int,
    ttl_seconds: int,
) -> tuple[SyncBootstrapManifestRead, list[list[SyncEventEnvelope]]]:
    issued_at = _utc(credential_issued_at, field="credential_issued_at")
    expires_at = bootstrap_expires_at(
        credential_issued_at=issued_at,
        credential_expires_at=credential_expires_at,
        ttl_seconds=ttl_seconds,
    )
    require_checksum(root_source_checksum, field="root_source_checksum")
    require_checksum(root_projection_checksum, field="root_projection_checksum")
    require_checksum(source_checksum, field="source_checksum")
    require_checksum(projection_checksum, field="projection_checksum")
    if chunk_size < 1 or chunk_size > 100:
        raise BootstrapValidationError("Bootstrap chunk size is invalid")
    if checkpoint_sequence != len(events):
        raise BootstrapValidationError("Bootstrap event history is not contiguous")

    chunks = [events[offset : offset + chunk_size] for offset in range(0, len(events), chunk_size)]
    descriptors: list[SyncBootstrapChunkDescriptor] = []
    previous_source = root_source_checksum
    previous_projection = root_projection_checksum
    expected_sequence = 1
    for index, chunk in enumerate(chunks):
        for event in chunk:
            if (
                event.tenant_id != tenant_id
                or event.branch_id != branch_id
                or event.origin_node_id != origin_node_id
                or event.writer_epoch != writer_epoch
                or event.sequence != expected_sequence
            ):
                raise BootstrapValidationError("Bootstrap event scope or sequence is invalid")
            expected_sequence += 1
        last = chunk[-1]
        descriptors.append(
            SyncBootstrapChunkDescriptor(
                index=index,
                first_sequence=chunk[0].sequence,
                last_sequence=last.sequence,
                event_count=len(chunk),
                after_source_checksum=previous_source,
                after_projection_checksum=previous_projection,
                source_checksum=last.stream_checksum,
                projection_checksum=last.projection_checksum,
                payload_hash=chunk_payload_hash(index=index, events=chunk),
            )
        )
        previous_source = last.stream_checksum
        previous_projection = last.projection_checksum

    if previous_source != source_checksum or previous_projection != projection_checksum:
        raise BootstrapValidationError("Bootstrap terminal checkpoint does not match enrollment")
    snapshot_hash = _snapshot_hash(descriptors)
    bootstrap_id = bootstrap_id_for(
        edge_node_id=edge_node_id,
        tenant_id=tenant_id,
        branch_id=branch_id,
        credential_kid=credential_kid,
        origin_node_id=origin_node_id,
        writer_epoch=writer_epoch,
        checkpoint_sequence=checkpoint_sequence,
        issued_at=issued_at,
    )
    manifest = SyncBootstrapManifest(
        bootstrap_id=bootstrap_id,
        edge_node_id=edge_node_id,
        tenant_id=tenant_id,
        branch_id=branch_id,
        credential_kid=credential_kid,
        origin_node_id=origin_node_id,
        writer_epoch=writer_epoch,
        root_source_checksum=root_source_checksum,
        root_projection_checksum=root_projection_checksum,
        checkpoint_sequence=checkpoint_sequence,
        source_checksum=source_checksum,
        projection_checksum=projection_checksum,
        snapshot_hash=snapshot_hash,
        chunk_size=chunk_size,
        chunks=descriptors,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return (
        SyncBootstrapManifestRead(
            manifest=manifest,
            manifest_hash=manifest_hash(manifest),
            signature_algorithm=SIGNATURE_ALGORITHM,
            signature=sign_manifest(manifest, credential_digest=credential_digest),
        ),
        chunks,
    )


def verify_manifest(
    signed: SyncBootstrapManifestRead,
    *,
    credential: str,
    now: datetime,
) -> SyncBootstrapManifest:
    parsed = parse_edge_credential(credential)
    manifest = signed.manifest
    if signed.signature_algorithm != SIGNATURE_ALGORITHM:
        raise BootstrapValidationError("Unsupported bootstrap signature algorithm")
    if parsed.kid != manifest.credential_kid:
        raise BootstrapValidationError("Bootstrap credential scope does not match")
    expected_hash = manifest_hash(manifest)
    if not hmac.compare_digest(expected_hash, signed.manifest_hash):
        raise BootstrapValidationError("Bootstrap manifest hash does not match")
    expected_signature = sign_manifest(manifest, credential_digest=parsed.digest)
    if not hmac.compare_digest(expected_signature, signed.signature):
        raise BootstrapValidationError("Bootstrap signature does not match")

    now = _utc(now, field="now")
    issued_at = _utc(manifest.issued_at, field="issued_at")
    expires_at = _utc(manifest.expires_at, field="expires_at")
    if expires_at <= issued_at or now >= expires_at:
        raise BootstrapValidationError("Bootstrap manifest has expired")
    if issued_at > now + timedelta(minutes=5):
        raise BootstrapValidationError("Bootstrap manifest was issued in the future")

    expected_sequence = 1
    previous_source = manifest.root_source_checksum
    previous_projection = manifest.root_projection_checksum
    for index, descriptor in enumerate(manifest.chunks):
        if (
            descriptor.index != index
            or descriptor.first_sequence != expected_sequence
            or descriptor.event_count != descriptor.last_sequence - descriptor.first_sequence + 1
            or descriptor.after_source_checksum != previous_source
            or descriptor.after_projection_checksum != previous_projection
        ):
            raise BootstrapValidationError("Bootstrap chunk chain is invalid")
        expected_sequence = descriptor.last_sequence + 1
        previous_source = descriptor.source_checksum
        previous_projection = descriptor.projection_checksum
    if (
        expected_sequence - 1 != manifest.checkpoint_sequence
        or previous_source != manifest.source_checksum
        or previous_projection != manifest.projection_checksum
        or _snapshot_hash(manifest.chunks) != manifest.snapshot_hash
    ):
        raise BootstrapValidationError("Bootstrap checkpoint chain is invalid")
    return manifest


def verify_chunk(
    manifest: SyncBootstrapManifest,
    chunk: SyncBootstrapChunkRead,
) -> SyncBootstrapChunkDescriptor:
    if chunk.bootstrap_id != manifest.bootstrap_id or chunk.chunk_index >= len(manifest.chunks):
        raise BootstrapValidationError("Bootstrap chunk scope is invalid")
    descriptor = manifest.chunks[chunk.chunk_index]
    payload_hash = chunk_payload_hash(index=chunk.chunk_index, events=chunk.events)
    if (
        chunk.payload_hash != descriptor.payload_hash
        or not hmac.compare_digest(payload_hash, descriptor.payload_hash)
        or len(chunk.events) != descriptor.event_count
    ):
        raise BootstrapValidationError("Bootstrap chunk hash does not match")
    for offset, event in enumerate(chunk.events):
        if (
            event.tenant_id != manifest.tenant_id
            or event.branch_id != manifest.branch_id
            or event.origin_node_id != manifest.origin_node_id
            or event.writer_epoch != manifest.writer_epoch
            or event.sequence != descriptor.first_sequence + offset
        ):
            raise BootstrapValidationError("Bootstrap chunk event scope is invalid")
    return descriptor


def chunk_as_pull(
    *,
    manifest: SyncBootstrapManifest,
    chunk: SyncBootstrapChunkRead,
) -> SyncPullResponse:
    descriptor = verify_chunk(manifest, chunk)
    return SyncPullResponse(
        edge_node_id=manifest.edge_node_id,
        tenant_id=manifest.tenant_id,
        branch_id=manifest.branch_id,
        origin_node_id=manifest.origin_node_id,
        writer_epoch=manifest.writer_epoch,
        effective_after_sequence=descriptor.first_sequence - 1,
        after_source_checksum=descriptor.after_source_checksum,
        after_projection_checksum=descriptor.after_projection_checksum,
        cloud_last_sequence=manifest.checkpoint_sequence,
        events=chunk.events,
        has_more=descriptor.last_sequence < manifest.checkpoint_sequence,
    )
