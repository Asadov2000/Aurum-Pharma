"""Unsupported retail events fail closed at each existing outgoing read path."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from app.core.errors import BusinessRuleError
from app.domains.sync import service as sync_service
from app.domains.sync.bootstrap import BootstrapScope, bootstrap_id_for
from app.domains.sync.models import SyncOutboxEvent, SyncStream
from app.domains.sync.repository import SyncCloudRepository
from app.domains.sync.service import SyncCloudService, _require_compatible_delivery
from tests.domains.pos.test_retail_sale_events import Kind, event

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def scope() -> BootstrapScope:
    source = event()
    return BootstrapScope(
        edge_node_id=UUID(int=91),
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        credential_kid=UUID(int=92),
        credential_digest="d" * 64,
        credential_issued_at=NOW,
        credential_expires_at=NOW + timedelta(days=1),
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
        root_source_checksum="a" * 64,
        root_projection_checksum="b" * 64,
        checkpoint_sequence=3,
        source_checksum=source.stream_checksum,
        projection_checksum=source.projection_checksum,
    )


class Repository:
    def __init__(self, kind: Kind, placement: str) -> None:
        retail = SyncOutboxEvent(**event(kind).model_dump(mode="python"))
        self.rows = (
            [SyncOutboxEvent(event_type="pos.sale.completed.v1"), retail]
            if placement == "lookahead"
            else [retail]
        )
        self.calls: list[dict[str, object]] = []

    async def get_stream(self, **arguments: object) -> SyncStream:
        current = scope()
        assert arguments == {"tenant_id": current.tenant_id, "branch_id": current.branch_id}
        return SyncStream(
            writer_node_id=current.origin_node_id,
            writer_epoch=current.writer_epoch,
            last_sequence=current.checkpoint_sequence,
        )

    async def list_events(self, **arguments: object) -> list[SyncOutboxEvent]:
        self.calls.append(arguments)
        return self.rows


@pytest.mark.parametrize("kind", ["physical", "mixed"])
@pytest.mark.parametrize("path", ["pull", "manifest", "chunk"])
@pytest.mark.parametrize("placement", ["first", "lookahead"])
async def test_retail_events_are_rejected_without_filtering_or_returning_partial_history(
    monkeypatch: pytest.MonkeyPatch, kind: Kind, path: str, placement: str
) -> None:
    monkeypatch.setattr(sync_service, "utc_now", lambda: NOW)
    repository = Repository(kind, placement)
    cloud = SyncCloudService(cast(SyncCloudRepository, repository))
    current = scope()
    with pytest.raises(BusinessRuleError, match="compatible receiver"):
        if path == "pull":
            await cloud.pull(
                edge_node_id=current.edge_node_id,
                tenant_id=current.tenant_id,
                branch_id=current.branch_id,
                shadow_start_sequence=0,
                shadow_start_checksum=current.root_source_checksum,
                shadow_start_projection_checksum=current.root_projection_checksum,
                after_sequence=0,
                limit=1,
            )
        elif path == "manifest":
            await cloud.bootstrap_manifest(scope=current)
        else:
            identifier = bootstrap_id_for(
                edge_node_id=current.edge_node_id,
                tenant_id=current.tenant_id,
                branch_id=current.branch_id,
                credential_kid=current.credential_kid,
                origin_node_id=current.origin_node_id,
                writer_epoch=current.writer_epoch,
                checkpoint_sequence=current.checkpoint_sequence,
                issued_at=current.credential_issued_at,
            )
            await cloud.bootstrap_chunk(bootstrap_id=identifier, chunk_index=0, scope=current)
    assert len(repository.calls) == 1
    assert repository.calls[0]["origin_node_id"] == current.origin_node_id
    assert repository.calls[0]["writer_epoch"] == current.writer_epoch
    assert len(repository.rows) == (2 if placement == "lookahead" else 1)


def test_delivery_gate_keeps_existing_v1_and_empty_history_compatible() -> None:
    legacy = [SyncOutboxEvent(event_type="pos.sale.completed.v1")]
    assert _require_compatible_delivery(legacy) is None
    assert _require_compatible_delivery([]) is None
    assert legacy[0].event_type == "pos.sale.completed.v1"
