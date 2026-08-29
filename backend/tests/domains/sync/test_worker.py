"""Worker behavior when Edge has stopped a sync stream."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from app.core.config import get_settings
from app.core.time import utc_now
from app.domains.sync import worker
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
