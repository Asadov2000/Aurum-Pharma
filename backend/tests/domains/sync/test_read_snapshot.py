"""Strict cryptographic contract for read-only Edge snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.sync.credentials import EdgeCredential, issue_edge_credential
from app.domains.sync.read_snapshot import (
    ChunkRead,
    ComponentDescriptor,
    ManifestRead,
    ReadSnapshotComponent,
    ReadSnapshotItem,
    ReadSnapshotScope,
    ReadSnapshotValidationError,
    build_manifest,
    verify_chunk,
    verify_manifest,
)


def _scope() -> ReadSnapshotScope:
    return ReadSnapshotScope(
        edge_node_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id=UUID("00000000-0000-0000-0000-000000000002"),
        branch_id=UUID("00000000-0000-0000-0000-000000000003"),
        source_revision=7,
    )


def _snapshot() -> tuple[EdgeCredential, ManifestRead, list[ChunkRead]]:
    credential = EdgeCredential(
        kid=UUID("00000000-0000-0000-0000-000000000005"),
        secret="1" * 64,
    )
    envelope, chunks = build_manifest(
        _scope(),
        {
            "foundation": [[{"currency": "TJS", "enabled": True}]],
            "catalog": [
                [{"id": "p-1", "name": "Парацетамол"}],
                [{"id": "p-2", "name": "Ибупрофен"}],
            ],
            "inventory": [],
        },
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        source_generated_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        snapshot_id=UUID("00000000-0000-0000-0000-000000000004"),
    )
    return credential, envelope, chunks


def test_read_snapshot_round_trip_and_empty_component_chunk_zero() -> None:
    credential, envelope, chunks = _snapshot()

    manifest = verify_manifest(envelope, credential=credential.token)
    descriptors = [verify_chunk(manifest, chunk) for chunk in reversed(chunks)]

    assert [component.component for component in manifest.components] == [
        "foundation",
        "catalog",
        "inventory",
    ]
    inventory = next(chunk for chunk in chunks if chunk.component == "inventory")
    assert inventory.chunk_index == 0
    assert inventory.item_count == 0
    assert inventory.payload == ()
    assert len(descriptors) == 4
    assert manifest.source_generated_at == datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    assert envelope.manifest_hash == (
        "1afb118ab6074cea9fa55b39b7de9868005d2e99ff5f69c0001c716388c3bb5a"
    )


def test_manifest_rejects_signature_and_manifest_tampering() -> None:
    credential, envelope, _ = _snapshot()
    tampered_signature = envelope.model_copy(update={"signature": "0" * 64})

    with pytest.raises(ReadSnapshotValidationError, match="signature"):
        verify_manifest(tampered_signature, credential=credential.token)

    tampered_manifest = envelope.model_copy(
        update={
            "manifest": envelope.manifest.model_copy(update={"source_revision": 8}),
        }
    )
    with pytest.raises(ReadSnapshotValidationError, match="Manifest hash"):
        verify_manifest(tampered_manifest, credential=credential.token)


def test_manifest_requires_exact_components_and_contiguous_descriptors() -> None:
    credential, envelope, _ = _snapshot()
    catalog = envelope.manifest.components[1]
    malformed = catalog.model_dump(mode="json")
    malformed["chunks"][1]["chunk_index"] = 2
    with pytest.raises(ValidationError, match="contiguous"):
        ComponentDescriptor.model_validate(malformed)

    incomplete: dict[ReadSnapshotComponent, list[list[ReadSnapshotItem]]] = {
        "foundation": [],
        "catalog": [],
    }
    with pytest.raises(ReadSnapshotValidationError, match="exact component set"):
        build_manifest(
            _scope(),
            incomplete,
            credential_kid=credential.kid,
            credential_digest=credential.digest,
            source_generated_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        )


def test_chunk_rejects_payload_and_scope_tampering() -> None:
    credential, envelope, chunks = _snapshot()
    manifest = verify_manifest(envelope, credential=credential.token)
    catalog = next(chunk for chunk in chunks if chunk.component == "catalog")
    tampered_payload = catalog.model_copy(update={"payload": ({"id": "other"},)})

    with pytest.raises(ReadSnapshotValidationError, match="payload hash"):
        verify_chunk(manifest, tampered_payload)

    wrong_scope = catalog.model_copy(
        update={"branch_id": UUID("00000000-0000-0000-0000-000000000099")}
    )
    with pytest.raises(ReadSnapshotValidationError, match="scope"):
        verify_chunk(manifest, wrong_scope)


def test_contracts_forbid_extra_fields() -> None:
    _, envelope, chunks = _snapshot()

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ManifestRead.model_validate({**envelope.model_dump(mode="json"), "unexpected": True})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ChunkRead.model_validate({**chunks[0].model_dump(mode="json"), "unexpected": True})


def test_non_empty_component_cannot_contain_an_empty_chunk() -> None:
    credential = issue_edge_credential()

    with pytest.raises(ReadSnapshotValidationError, match="empty chunks"):
        build_manifest(
            _scope(),
            {
                "foundation": [[{"currency": "TJS"}]],
                "catalog": [[], [{"id": "p-1"}]],
                "inventory": [],
            },
            credential_kid=credential.kid,
            credential_digest=credential.digest,
            source_generated_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        )


def test_component_descriptor_forbids_unknown_fields() -> None:
    _, envelope, _ = _snapshot()
    descriptor = envelope.manifest.components[0]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ComponentDescriptor.model_validate(
            {**descriptor.model_dump(mode="json"), "unexpected": True}
        )
