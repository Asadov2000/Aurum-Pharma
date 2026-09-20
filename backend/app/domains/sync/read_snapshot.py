"""Authenticated contract for immutable read-only Edge snapshots."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.integrity import canonical_json_bytes, canonical_json_hash, require_checksum

type ReadSnapshotComponent = Literal["foundation", "catalog", "inventory"]
type ReadSnapshotItem = dict[str, JsonValue]

REQUIRED_COMPONENTS: tuple[ReadSnapshotComponent, ...] = (
    "foundation",
    "catalog",
    "inventory",
)
SIGNATURE_ALGORITHM = "hmac-sha256-edge-read-snapshot-v1"

_KEY_DOMAIN = b"aurum-edge-read-snapshot-mac-v1\x00"
_PAYLOAD_DOMAIN = "aurum:edge-read-snapshot-payload:v1"
_COMPONENT_DOMAIN = "aurum:edge-read-snapshot-component:v1"
_SNAPSHOT_DOMAIN = "aurum:edge-read-snapshot:v1"


class ReadSnapshotValidationError(ValueError):
    """The snapshot is incomplete, corrupted, or outside the expected scope."""


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReadSnapshotScope(_StrictContract):
    edge_node_id: UUID
    tenant_id: UUID
    branch_id: UUID
    source_revision: int = Field(ge=1)


class ChunkDescriptor(_StrictContract):
    schema_version: Literal[1] = 1
    chunk_index: int = Field(ge=0)
    item_count: int = Field(ge=0)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ComponentDescriptor(_StrictContract):
    schema_version: Literal[1] = 1
    component: ReadSnapshotComponent
    item_count: int = Field(ge=0)
    chunk_count: int = Field(ge=1)
    chunks: tuple[ChunkDescriptor, ...] = Field(min_length=1)
    component_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_chunk_coverage(self) -> Self:
        indexes = [chunk.chunk_index for chunk in self.chunks]
        if indexes != list(range(len(self.chunks))):
            raise ValueError("chunk descriptors must be contiguous")
        if self.chunk_count != len(self.chunks):
            raise ValueError("component chunk count does not match")
        item_count = sum(chunk.item_count for chunk in self.chunks)
        if self.item_count != item_count:
            raise ValueError("component item count does not match")
        if item_count == 0 and not (
            len(self.chunks) == 1
            and self.chunks[0].chunk_index == 0
            and self.chunks[0].item_count == 0
        ):
            raise ValueError("an empty component must be represented by chunk 0")
        if item_count > 0 and any(chunk.item_count == 0 for chunk in self.chunks):
            raise ValueError("non-empty components cannot contain empty chunks")
        return self


class Manifest(_StrictContract):
    schema_version: Literal[1] = 1
    profile: Literal["read_only_v1"] = "read_only_v1"
    snapshot_id: UUID
    edge_node_id: UUID
    tenant_id: UUID
    branch_id: UUID
    source_revision: int = Field(ge=1)
    source_generated_at: datetime
    credential_kid: UUID
    components: tuple[ComponentDescriptor, ...] = Field(min_length=3, max_length=3)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("source_generated_at")
    @classmethod
    def normalize_source_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("source_generated_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_component_set(self) -> Self:
        if tuple(component.component for component in self.components) != REQUIRED_COMPONENTS:
            raise ValueError("snapshot must contain the exact canonical component set")
        return self


class ManifestRead(_StrictContract):
    manifest: Manifest
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_algorithm: Literal["hmac-sha256-edge-read-snapshot-v1"]
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChunkRead(_StrictContract):
    schema_version: Literal[1] = 1
    snapshot_id: UUID
    edge_node_id: UUID
    tenant_id: UUID
    branch_id: UUID
    source_revision: int = Field(ge=1)
    component: ReadSnapshotComponent
    chunk_index: int = Field(ge=0)
    item_count: int = Field(ge=0)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: tuple[ReadSnapshotItem, ...]


def chunk_payload_hash(
    *,
    snapshot_id: UUID,
    scope: ReadSnapshotScope,
    component: ReadSnapshotComponent,
    chunk_index: int,
    payload: Sequence[ReadSnapshotItem],
) -> str:
    """Hash the canonical payload together with all routing metadata."""

    return canonical_json_hash(
        {
            "domain": _PAYLOAD_DOMAIN,
            "schema_version": 1,
            "snapshot_id": str(snapshot_id),
            "edge_node_id": str(scope.edge_node_id),
            "tenant_id": str(scope.tenant_id),
            "branch_id": str(scope.branch_id),
            "source_revision": scope.source_revision,
            "component": component,
            "chunk_index": chunk_index,
            "item_count": len(payload),
            "payload": list(payload),
        }
    )


def _component_hash(
    component: ReadSnapshotComponent,
    descriptors: Sequence[ChunkDescriptor],
) -> str:
    return canonical_json_hash(
        {
            "domain": _COMPONENT_DOMAIN,
            "schema_version": 1,
            "component": component,
            "item_count": sum(descriptor.item_count for descriptor in descriptors),
            "chunks": [descriptor.model_dump(mode="json") for descriptor in descriptors],
        }
    )


def _snapshot_hash(
    *,
    snapshot_id: UUID,
    scope: ReadSnapshotScope,
    credential_kid: UUID,
    components: Sequence[ComponentDescriptor],
) -> str:
    return canonical_json_hash(
        {
            "domain": _SNAPSHOT_DOMAIN,
            "schema_version": 1,
            "profile": "read_only_v1",
            "snapshot_id": str(snapshot_id),
            "edge_node_id": str(scope.edge_node_id),
            "tenant_id": str(scope.tenant_id),
            "branch_id": str(scope.branch_id),
            "source_revision": scope.source_revision,
            "credential_kid": str(credential_kid),
            "components": [component.model_dump(mode="json") for component in components],
        }
    )


def _manifest_hash(manifest: Manifest) -> str:
    return canonical_json_hash(manifest.model_dump(mode="json"))


def _mac_key(credential_digest: str) -> bytes:
    try:
        require_checksum(credential_digest, field="credential_digest")
        digest = bytes.fromhex(credential_digest)
    except ValueError as exc:
        raise ReadSnapshotValidationError("Credential digest is invalid") from exc
    return hmac.new(digest, _KEY_DOMAIN, hashlib.sha256).digest()


def _signature(manifest: Manifest, *, credential_digest: str) -> str:
    return hmac.new(
        _mac_key(credential_digest),
        canonical_json_bytes(manifest.model_dump(mode="json")),
        hashlib.sha256,
    ).hexdigest()


def _validate_component_descriptors(
    components: Sequence[ComponentDescriptor],
) -> None:
    names = [component.component for component in components]
    if tuple(names) != REQUIRED_COMPONENTS:
        raise ReadSnapshotValidationError("Snapshot must contain the exact canonical component set")
    for component in components:
        indexes = [chunk.chunk_index for chunk in component.chunks]
        if indexes != list(range(len(component.chunks))):
            raise ReadSnapshotValidationError("Chunk descriptors must be contiguous")
        if component.chunk_count != len(component.chunks):
            raise ReadSnapshotValidationError("Component chunk count does not match")
        item_count = sum(chunk.item_count for chunk in component.chunks)
        if component.item_count != item_count:
            raise ReadSnapshotValidationError("Component item count does not match")
        if item_count == 0 and not (
            len(component.chunks) == 1
            and component.chunks[0].chunk_index == 0
            and component.chunks[0].item_count == 0
        ):
            raise ReadSnapshotValidationError("An empty component must be represented by chunk 0")
        if item_count > 0 and any(chunk.item_count == 0 for chunk in component.chunks):
            raise ReadSnapshotValidationError("Non-empty components cannot contain empty chunks")
        expected_hash = _component_hash(component.component, component.chunks)
        if not hmac.compare_digest(expected_hash, component.component_hash):
            raise ReadSnapshotValidationError("Component hash does not match")


def build_manifest(
    scope: ReadSnapshotScope,
    component_chunks: Mapping[
        ReadSnapshotComponent,
        Sequence[Sequence[ReadSnapshotItem]],
    ],
    *,
    credential_kid: UUID,
    credential_digest: str,
    source_generated_at: datetime,
    snapshot_id: UUID | None = None,
) -> tuple[ManifestRead, list[ChunkRead]]:
    """Build a signed manifest and canonical chunks for one source revision."""

    if set(component_chunks) != set(REQUIRED_COMPONENTS):
        raise ReadSnapshotValidationError("Snapshot requires the exact component set")
    snapshot_id = snapshot_id or uuid4()
    chunks: list[ChunkRead] = []
    components: list[ComponentDescriptor] = []
    for component in REQUIRED_COMPONENTS:
        raw_chunks = component_chunks[component]
        normalized_chunks: Sequence[Sequence[ReadSnapshotItem]] = raw_chunks or ((),)
        if len(normalized_chunks) > 1 and any(not payload for payload in normalized_chunks):
            raise ReadSnapshotValidationError("Non-empty components cannot contain empty chunks")
        descriptors: list[ChunkDescriptor] = []
        for chunk_index, raw_payload in enumerate(normalized_chunks):
            payload = tuple(dict(item) for item in raw_payload)
            payload_hash = chunk_payload_hash(
                snapshot_id=snapshot_id,
                scope=scope,
                component=component,
                chunk_index=chunk_index,
                payload=payload,
            )
            descriptor = ChunkDescriptor(
                chunk_index=chunk_index,
                item_count=len(payload),
                payload_hash=payload_hash,
            )
            descriptors.append(descriptor)
            chunks.append(
                ChunkRead(
                    snapshot_id=snapshot_id,
                    edge_node_id=scope.edge_node_id,
                    tenant_id=scope.tenant_id,
                    branch_id=scope.branch_id,
                    source_revision=scope.source_revision,
                    component=component,
                    chunk_index=chunk_index,
                    item_count=len(payload),
                    payload_hash=payload_hash,
                    payload=payload,
                )
            )
        components.append(
            ComponentDescriptor(
                component=component,
                item_count=sum(chunk.item_count for chunk in descriptors),
                chunk_count=len(descriptors),
                chunks=tuple(descriptors),
                component_hash=_component_hash(component, descriptors),
            )
        )
    manifest = Manifest(
        snapshot_id=snapshot_id,
        edge_node_id=scope.edge_node_id,
        tenant_id=scope.tenant_id,
        branch_id=scope.branch_id,
        source_revision=scope.source_revision,
        source_generated_at=source_generated_at,
        credential_kid=credential_kid,
        components=tuple(components),
        snapshot_hash=_snapshot_hash(
            snapshot_id=snapshot_id,
            scope=scope,
            credential_kid=credential_kid,
            components=components,
        ),
    )
    return (
        ManifestRead(
            manifest=manifest,
            manifest_hash=_manifest_hash(manifest),
            signature_algorithm=SIGNATURE_ALGORITHM,
            signature=_signature(manifest, credential_digest=credential_digest),
        ),
        chunks,
    )


def verify_manifest(envelope: ManifestRead, *, credential: str) -> Manifest:
    """Verify signature, hashes, scope binding, and complete component coverage."""

    try:
        parsed = parse_edge_credential(credential)
    except ValueError as exc:
        raise ReadSnapshotValidationError("Edge credential is invalid") from exc
    manifest = envelope.manifest
    if envelope.signature_algorithm != SIGNATURE_ALGORITHM:
        raise ReadSnapshotValidationError("Snapshot signature algorithm is unsupported")
    if parsed.kid != manifest.credential_kid:
        raise ReadSnapshotValidationError("Snapshot credential scope does not match")
    expected_manifest_hash = _manifest_hash(manifest)
    if not hmac.compare_digest(expected_manifest_hash, envelope.manifest_hash):
        raise ReadSnapshotValidationError("Manifest hash does not match")
    if not hmac.compare_digest(
        _signature(manifest, credential_digest=parsed.digest),
        envelope.signature,
    ):
        raise ReadSnapshotValidationError("Manifest signature does not match")
    _validate_component_descriptors(manifest.components)
    scope = ReadSnapshotScope(
        edge_node_id=manifest.edge_node_id,
        tenant_id=manifest.tenant_id,
        branch_id=manifest.branch_id,
        source_revision=manifest.source_revision,
    )
    expected_snapshot_hash = _snapshot_hash(
        snapshot_id=manifest.snapshot_id,
        scope=scope,
        credential_kid=manifest.credential_kid,
        components=manifest.components,
    )
    if not hmac.compare_digest(expected_snapshot_hash, manifest.snapshot_hash):
        raise ReadSnapshotValidationError("Snapshot hash does not match")
    return manifest


def verify_chunk(manifest: Manifest, chunk: ChunkRead) -> ChunkDescriptor:
    """Verify one payload against its scope-bound manifest descriptor."""

    if (
        chunk.snapshot_id != manifest.snapshot_id
        or chunk.edge_node_id != manifest.edge_node_id
        or chunk.tenant_id != manifest.tenant_id
        or chunk.branch_id != manifest.branch_id
        or chunk.source_revision != manifest.source_revision
    ):
        raise ReadSnapshotValidationError("Chunk scope does not match the manifest")
    component = next(
        (item for item in manifest.components if item.component == chunk.component),
        None,
    )
    if component is None or chunk.chunk_index >= len(component.chunks):
        raise ReadSnapshotValidationError("Chunk descriptor is missing")
    descriptor = component.chunks[chunk.chunk_index]
    if descriptor.chunk_index != chunk.chunk_index:
        raise ReadSnapshotValidationError("Chunk descriptor index does not match")
    if descriptor.item_count != chunk.item_count or chunk.item_count != len(chunk.payload):
        raise ReadSnapshotValidationError("Chunk item count does not match")
    scope = ReadSnapshotScope(
        edge_node_id=manifest.edge_node_id,
        tenant_id=manifest.tenant_id,
        branch_id=manifest.branch_id,
        source_revision=manifest.source_revision,
    )
    expected_payload_hash = chunk_payload_hash(
        snapshot_id=manifest.snapshot_id,
        scope=scope,
        component=chunk.component,
        chunk_index=chunk.chunk_index,
        payload=chunk.payload,
    )
    if not hmac.compare_digest(expected_payload_hash, chunk.payload_hash):
        raise ReadSnapshotValidationError("Chunk payload hash does not match")
    if not hmac.compare_digest(descriptor.payload_hash, chunk.payload_hash):
        raise ReadSnapshotValidationError("Chunk descriptor hash does not match")
    return descriptor
