"""Worker behavior when Edge has stopped a sync stream."""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest

from app.core.config import get_settings
from app.core.time import utc_now
from app.domains.sync import worker
from app.domains.sync.credentials import EdgeCredential
from app.domains.sync.read_snapshot import (
    ReadSnapshotScope,
    ReadSnapshotValidationError,
    build_manifest,
)
from app.domains.sync.schemas import (
    EdgeApplyResult,
    SyncPullResponse,
    SyncQuarantineIncidentRead,
    SyncQuarantineIncidentRequest,
)


async def test_halted_cycle_reports_incident_and_never_reports_healthy_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pull = SyncPullResponse(
        edge_node_id=uuid4(),
        tenant_id=uuid4(),
        branch_id=uuid4(),
        origin_node_id=uuid4(),
        writer_epoch=1,
        effective_after_sequence=0,
        after_source_checksum="0" * 64,
        after_projection_checksum="0" * 64,
        cloud_last_sequence=1,
        events=[],
        has_more=False,
    )
    incident = SyncQuarantineIncidentRequest(
        incident_id=uuid4(),
        origin_node_id=pull.origin_node_id,
        writer_epoch=1,
        cursor_status="quarantined",
        reason_code="operation_id_collision",
        last_applied_sequence=0,
        source_checksum="0" * 64,
        projection_checksum="0" * 64,
        observed_at=utc_now(),
        evidence_hash="a" * 64,
    )
    result = EdgeApplyResult(
        applied=0,
        duplicates=0,
        last_sequence=0,
        source_checksum="0" * 64,
        projection_checksum="0" * 64,
        status="quarantined",
        incident=incident,
    )
    calls = {"incident": 0, "checkpoint": 0}

    async def fake_pull(*args: object, **kwargs: object) -> SyncPullResponse:
        return pull

    async def fake_bootstrap(*args: object, **kwargs: object) -> None:
        return None

    async def fake_apply(value: SyncPullResponse) -> EdgeApplyResult:
        assert value is pull
        return result

    async def fake_incident(*args: object, **kwargs: object) -> SyncQuarantineIncidentRead:
        calls["incident"] += 1
        return SyncQuarantineIncidentRead(
            **incident.model_dump(),
            received_at=utc_now(),
            replayed=False,
        )

    async def fake_checkpoint(*args: object, **kwargs: object) -> None:
        calls["checkpoint"] += 1

    monkeypatch.setattr(worker, "_pull", fake_pull)
    monkeypatch.setattr(worker, "_bootstrap_if_required", fake_bootstrap)
    monkeypatch.setattr(worker, "_apply_pull", fake_apply)
    monkeypatch.setattr(worker, "_report_incident", fake_incident)
    monkeypatch.setattr(worker, "_report", fake_checkpoint)

    async with httpx.AsyncClient() as client:
        await worker.run_cycle(client, get_settings(), "development-credential")

    assert calls == {"incident": 1, "checkpoint": 0}


async def test_read_snapshot_downloads_every_verified_chunk_before_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = EdgeCredential(
        kid=UUID("10000000-0000-0000-0000-000000000001"),
        secret="1" * 64,
    )
    envelope, chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=UUID("10000000-0000-0000-0000-000000000002"),
            tenant_id=UUID("10000000-0000-0000-0000-000000000003"),
            branch_id=UUID("10000000-0000-0000-0000-000000000004"),
            source_revision=1,
        ),
        {
            "foundation": [[{"currency": "TJS"}]],
            "catalog": [[{"id": "product-1"}]],
            "inventory": [],
        },
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        source_generated_at=utc_now(),
    )
    installed: list[dict[str, object]] = []

    async def no_active_snapshot(*args: object, **kwargs: object) -> None:
        return None

    async def capture_install(*args: object, **kwargs: object) -> None:
        installed.append(dict(kwargs))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/sync/read-snapshot/manifest":
            return httpx.Response(200, json=envelope.model_dump(mode="json"))
        if request.url.path.endswith("/chunks"):
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200,
                json=[chunk.model_dump(mode="json") for chunk in chunks[offset : offset + limit]],
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    monkeypatch.setattr(worker, "_active_read_snapshot_id", no_active_snapshot)
    monkeypatch.setattr(worker, "_install_read_snapshot", capture_install)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://cloud.example",
    ) as client:
        await worker._sync_read_snapshot(client, credential=credential.token)

    assert len(installed) == 1
    assert installed[0]["envelope"] == envelope
    assert installed[0]["chunks"] == chunks


async def test_read_snapshot_missing_on_cloud_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_called = False

    async def capture_install(*args: object, **kwargs: object) -> None:
        nonlocal install_called
        install_called = True

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    monkeypatch.setattr(worker, "_install_read_snapshot", capture_install)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://cloud.example",
    ) as client:
        await worker._sync_read_snapshot(client, credential="unused")

    assert not install_called


async def test_read_snapshot_over_request_limit_uses_bounded_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = EdgeCredential(kid=uuid4(), secret="3" * 64)
    envelope, chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=uuid4(),
            tenant_id=uuid4(),
            branch_id=uuid4(),
            source_revision=1,
        ),
        {
            "foundation": [[{"kind": "foundation"}]],
            "catalog": [[{"id": index}] for index in range(125)],
            "inventory": [[]],
        },
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        source_generated_at=utc_now(),
    )
    batch_requests = 0
    installed = False

    async def no_active_snapshot(*args: object, **kwargs: object) -> None:
        return None

    async def capture_install(*args: object, **kwargs: object) -> None:
        nonlocal installed
        installed = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal batch_requests
        if request.url.path == "/api/v1/sync/read-snapshot/manifest":
            return httpx.Response(200, json=envelope.model_dump(mode="json"))
        batch_requests += 1
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        return httpx.Response(
            200,
            json=[chunk.model_dump(mode="json") for chunk in chunks[offset : offset + limit]],
        )

    monkeypatch.setattr(worker, "_active_read_snapshot_id", no_active_snapshot)
    monkeypatch.setattr(worker, "_install_read_snapshot", capture_install)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://cloud.example",
    ) as client:
        await worker._sync_read_snapshot(client, credential=credential.token)

    assert installed is True
    assert len(chunks) > 120
    assert batch_requests == 7


async def test_read_snapshot_confirmation_refreshes_freshness_without_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = EdgeCredential(kid=uuid4(), secret="4" * 64)
    envelope, _ = build_manifest(
        ReadSnapshotScope(
            edge_node_id=uuid4(),
            tenant_id=uuid4(),
            branch_id=uuid4(),
            source_revision=1,
        ),
        {"foundation": [[]], "catalog": [[]], "inventory": [[]]},
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        source_generated_at=utc_now(),
    )
    confirmed = False

    async def active_snapshot(*args: object, **kwargs: object) -> UUID:
        return envelope.manifest.snapshot_id

    async def capture_confirmation(*args: object, **kwargs: object) -> None:
        nonlocal confirmed
        confirmed = True

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/sync/read-snapshot/manifest"
        return httpx.Response(200, json=envelope.model_dump(mode="json"))

    monkeypatch.setattr(worker, "_active_read_snapshot_id", active_snapshot)
    monkeypatch.setattr(worker, "_confirm_active_read_snapshot", capture_confirmation)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://cloud.example",
    ) as client:
        await worker._sync_read_snapshot(client, credential=credential.token)

    assert confirmed is True


async def test_read_snapshot_rejects_tampered_chunk_before_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = EdgeCredential(
        kid=UUID("20000000-0000-0000-0000-000000000001"),
        secret="2" * 64,
    )
    envelope, chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=UUID("20000000-0000-0000-0000-000000000002"),
            tenant_id=UUID("20000000-0000-0000-0000-000000000003"),
            branch_id=UUID("20000000-0000-0000-0000-000000000004"),
            source_revision=1,
        ),
        {
            "foundation": [[{"currency": "TJS"}]],
            "catalog": [[{"id": "product-1"}]],
            "inventory": [],
        },
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        source_generated_at=utc_now(),
    )
    first_chunk = chunks[0]
    tampered = first_chunk.model_copy(update={"payload": ({"currency": "USD"},)})
    downloaded_chunks = [tampered if chunk == first_chunk else chunk for chunk in chunks]
    install_called = False

    async def no_active_snapshot(*args: object, **kwargs: object) -> None:
        return None

    async def capture_install(*args: object, **kwargs: object) -> None:
        nonlocal install_called
        install_called = True

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/sync/read-snapshot/manifest":
            return httpx.Response(200, json=envelope.model_dump(mode="json"))
        if request.url.path.endswith("/chunks"):
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200,
                json=[
                    chunk.model_dump(mode="json")
                    for chunk in downloaded_chunks[offset : offset + limit]
                ],
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    monkeypatch.setattr(worker, "_active_read_snapshot_id", no_active_snapshot)
    monkeypatch.setattr(worker, "_install_read_snapshot", capture_install)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://cloud.example",
    ) as client:
        with pytest.raises(ReadSnapshotValidationError, match="payload hash"):
            await worker._sync_read_snapshot(client, credential=credential.token)

    assert not install_called
