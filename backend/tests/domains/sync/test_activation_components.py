"""Deterministic and fail-closed metadata for the future full bootstrap."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import pytest

from app.domains.sync.activation_bootstrap import ActivationSnapshotScope
from app.domains.sync.activation_components import (
    REQUIRED_COMPONENTS,
    ActivationComponentValidationError,
    build_component_descriptor,
    component_manifest_hash,
    full_snapshot_hash,
)
from app.domains.sync.schemas import (
    SyncActivationBootstrapChunkMetadata,
    SyncActivationBootstrapComponentMetadata,
    SyncActivationComponent,
)


def _chunks(
    component: SyncActivationComponent,
    *,
    indexes: Sequence[int] = (0,),
) -> list[SyncActivationBootstrapChunkMetadata]:
    return [
        SyncActivationBootstrapChunkMetadata(
            component=component,
            chunk_index=index,
            item_count=index + 1,
            payload_hash=f"{index + 1:064x}",
        )
        for index in indexes
    ]


def _full_descriptors() -> list[SyncActivationBootstrapComponentMetadata]:
    return [
        build_component_descriptor(component=component, chunks=_chunks(component))
        for component in REQUIRED_COMPONENTS
    ]


def test_component_hash_is_order_independent_and_stable() -> None:
    chunks = _chunks("catalog", indexes=(1, 0))

    descriptor = build_component_descriptor(component="catalog", chunks=chunks)

    assert descriptor.item_count == 3
    assert descriptor.chunk_count == 2
    assert descriptor.component_hash == (
        "1634dc867fca18d91692b22524313166ef7dcc658eb62f04a99373aa55bbc434"
    )


def test_component_rejects_non_contiguous_chunks() -> None:
    with pytest.raises(ActivationComponentValidationError, match="contiguous"):
        build_component_descriptor(component="inventory", chunks=_chunks("inventory", indexes=(1,)))


def test_component_manifest_requires_exact_component_set() -> None:
    descriptors = _full_descriptors()

    digest = component_manifest_hash(descriptors)

    assert digest == "73a8058ed26206780a005e8000286be4cd4e414d9268cddf3ee42366de4ee688"
    with pytest.raises(ActivationComponentValidationError, match="incomplete"):
        component_manifest_hash(descriptors[:-1])


def test_component_manifest_rejects_duplicates() -> None:
    descriptors = _full_descriptors()

    with pytest.raises(ActivationComponentValidationError, match="unique"):
        component_manifest_hash([*descriptors, descriptors[0]])


def test_full_snapshot_binds_component_root_to_activation_scope() -> None:
    scope = ActivationSnapshotScope(
        activation_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id=UUID("00000000-0000-0000-0000-000000000002"),
        branch_id=UUID("00000000-0000-0000-0000-000000000003"),
        edge_node_id=UUID("00000000-0000-0000-0000-000000000004"),
        register_id=UUID("00000000-0000-0000-0000-000000000005"),
        writer_epoch=2,
        previous_writer_epoch=1,
        previous_terminal_sequence=9,
        previous_terminal_source_checksum="a" * 64,
        previous_terminal_projection_checksum="b" * 64,
        receipt_baseline_seq=11,
    )

    digest = full_snapshot_hash(
        scope=scope,
        foundation_digest="c" * 64,
        component_manifest_digest="d" * 64,
    )

    assert digest == "2d862ba9724d10ce2cfa1c9737abf0c9491addecee152e611fa2df8d42888dcc"
    assert digest != full_snapshot_hash(
        scope=scope,
        foundation_digest="c" * 64,
        component_manifest_digest="e" * 64,
    )
