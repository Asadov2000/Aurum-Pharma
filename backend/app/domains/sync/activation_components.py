"""Deterministic metadata hashes for the future full activation bootstrap."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from app.domains.sync.activation_bootstrap import ActivationSnapshotScope
from app.domains.sync.schemas import (
    SyncActivationBootstrapChunkMetadata,
    SyncActivationBootstrapComponentMetadata,
    SyncActivationComponent,
)

REQUIRED_COMPONENTS: tuple[SyncActivationComponent, ...] = (
    "authorization",
    "catalog",
    "inventory",
    "offline_auth",
    "pos_materialization",
    "shift",
)
_COMPONENT_DOMAIN = "aurum:activation-bootstrap-component:v1"
_MANIFEST_DOMAIN = "aurum:activation-bootstrap-components:v1"
_SNAPSHOT_DOMAIN = "aurum:activation-full-snapshot:v1"


class ActivationComponentValidationError(ValueError):
    """Component metadata is incomplete, ambiguous, or internally inconsistent."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_component_descriptor(
    *,
    component: SyncActivationComponent,
    chunks: Sequence[SyncActivationBootstrapChunkMetadata],
) -> SyncActivationBootstrapComponentMetadata:
    """Validate chunk coverage and bind ordered chunk hashes into one descriptor."""

    if not chunks:
        raise ActivationComponentValidationError("A component requires at least one chunk")
    ordered = sorted(chunks, key=lambda chunk: chunk.chunk_index)
    expected_indexes = list(range(len(ordered)))
    if [chunk.chunk_index for chunk in ordered] != expected_indexes:
        raise ActivationComponentValidationError("Component chunk indexes must be contiguous")
    if any(chunk.component != component for chunk in ordered):
        raise ActivationComponentValidationError("Component chunk scope does not match")
    if any(chunk.schema_version != 1 for chunk in ordered):
        raise ActivationComponentValidationError("Component schema version is unsupported")

    item_count = sum(chunk.item_count for chunk in ordered)
    chunk_chain = "|".join(
        f"{chunk.chunk_index}:{chunk.item_count}:{chunk.payload_hash}" for chunk in ordered
    )
    component_hash = _sha256(
        f"{_COMPONENT_DOMAIN}:{component}:1:{item_count}:{len(ordered)}:{chunk_chain}"
    )
    return SyncActivationBootstrapComponentMetadata(
        component=component,
        item_count=item_count,
        chunk_count=len(ordered),
        component_hash=component_hash,
    )


def component_manifest_hash(
    components: Sequence[SyncActivationBootstrapComponentMetadata],
) -> str:
    """Bind the exact required component set into a deterministic full-profile root."""

    by_name = {component.component: component for component in components}
    if len(by_name) != len(components):
        raise ActivationComponentValidationError("Activation components must be unique")
    if set(by_name) != set(REQUIRED_COMPONENTS):
        raise ActivationComponentValidationError("Full activation component set is incomplete")

    descriptor_chain = "|".join(
        (
            f"{component.component}:{component.schema_version}:"
            f"{component.item_count}:{component.chunk_count}:{component.component_hash}"
        )
        for component in sorted(components, key=lambda item: item.component)
    )
    return _sha256(f"{_MANIFEST_DOMAIN}:{descriptor_chain}")


def full_snapshot_hash(
    *,
    scope: ActivationSnapshotScope,
    foundation_digest: str,
    component_manifest_digest: str,
) -> str:
    """Bind the full component root and foundation to one handover attempt."""

    seed = ":".join(
        (
            _SNAPSHOT_DOMAIN,
            "1",
            "cash_sale_v1_full_v1",
            "true",
            str(scope.activation_id),
            str(scope.tenant_id),
            str(scope.branch_id),
            str(scope.edge_node_id),
            str(scope.register_id),
            "cash_sale_v1",
            str(scope.writer_epoch),
            str(scope.previous_writer_epoch),
            str(scope.previous_terminal_sequence),
            scope.previous_terminal_source_checksum,
            scope.previous_terminal_projection_checksum,
            str(scope.receipt_baseline_seq),
            foundation_digest,
            component_manifest_digest,
        )
    )
    return _sha256(seed)
