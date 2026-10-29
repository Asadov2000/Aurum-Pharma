"""Inventory inbox persistence requires independent trust and never posts source stock."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import cast
from uuid import UUID

import pytest

from app.domains.sync.models import SyncPhysicalStockAnchor, SyncPhysicalStockProjection
from app.domains.sync.repository import SyncEdgeRepository
from app.domains.sync.schemas import SyncEventEnvelope, SyncPullResponse
from app.domains.sync.service import SyncEdgeApplyService
from tests.domains.sync.test_physical_stock_events import (
    PROJECTION,
    SOURCE,
    anchor,
    change,
    event,
)
from tests.domains.sync.test_retail_edge_apply import NODE, Repository
from tests.domains.sync.test_retail_edge_apply import event as retail_event


def pull(source: SyncEventEnvelope) -> SyncPullResponse:
    return SyncPullResponse(
        edge_node_id=NODE,
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
        effective_after_sequence=source.sequence - 1,
        after_source_checksum=SOURCE,
        after_projection_checksum=PROJECTION,
        cloud_last_sequence=source.sequence,
        events=[source],
        has_more=False,
    )


class StockRepository(Repository):
    def __init__(self, source: SyncEventEnvelope) -> None:
        super().__init__(retail_event())
        self.anchor = None
        self.cursor.tenant_id = source.tenant_id
        self.cursor.branch_id = source.branch_id
        self.stock_anchor: SyncPhysicalStockAnchor | None = SyncPhysicalStockAnchor(
            event_id=source.event_id,
            target_edge_node_id=NODE,
            **anchor(source).model_dump(),
        )
        self.stock_rows: dict[UUID, SyncPhysicalStockProjection] = {}

    async def get_physical_stock_anchor(self, **scope: object) -> SyncPhysicalStockAnchor | None:
        row = self.stock_anchor
        if row is None:
            return None
        expected: dict[str, object] = {
            "tenant_id": row.tenant_id,
            "branch_id": row.branch_id,
            "edge_node_id": row.target_edge_node_id,
        }
        if "event_id" in scope:
            expected["event_id"] = row.event_id
        else:
            expected.update(
                origin_node_id=row.origin_node_id,
                writer_epoch=row.writer_epoch,
                sequence=row.sequence,
            )
        return row if scope == expected else None

    async def get_physical_stock_projection(
        self, event_id: UUID
    ) -> SyncPhysicalStockProjection | None:
        return self.stock_rows.get(event_id)

    async def insert_physical_stock_projection(
        self, envelope: SyncEventEnvelope
    ) -> SyncPhysicalStockProjection:
        row = SyncPhysicalStockProjection(
            source_event_id=envelope.event_id,
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            origin_node_id=envelope.origin_node_id,
            writer_epoch=envelope.writer_epoch,
            sequence=envelope.sequence,
            operation_id=envelope.operation_id,
            source_id=envelope.aggregate_id,
            payload=deepcopy(envelope.payload),
            source_payload_hash=envelope.payload_hash,
            projection_hash=envelope.projection_hash,
        )
        self.stock_rows[envelope.event_id] = row
        if self.fail_at == "projection":
            raise RuntimeError("injected receive persistence failure")
        return row

    async def list_physical_stock_projections(
        self, **scope: object
    ) -> list[SyncPhysicalStockProjection]:
        return sorted(self.stock_rows.values(), key=lambda row: row.sequence)

    @asynccontextmanager
    async def event_savepoint(self) -> AsyncIterator[None]:
        before = dict(self.stock_rows)
        try:
            async with super().event_savepoint():
                yield
        except Exception:
            self.stock_rows = before
            raise


def receiver(repository: StockRepository) -> SyncEdgeApplyService:
    return SyncEdgeApplyService(cast(SyncEdgeRepository, repository))


@pytest.mark.parametrize(
    "kind",
    [
        "initialize",
        "legacy_transition",
        "dispense",
        "writeoff",
        "writeoff_cancelled",
        "block",
        "catalog_config",
        "baseline_required",
    ],
)
async def test_trusted_source_applies_and_replays_only_read_projection(kind: str) -> None:
    source = event(change(kind))
    repository = StockRepository(source)
    service = receiver(repository)
    applied = await service.apply(pull(source))
    assert applied.status == "synced" and applied.applied == 1
    assert repository.stock_rows[source.event_id].payload == source.payload
    assert not repository.projections
    verified = await service.verify_projection(
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
    )
    assert verified.status == "synced"
    replay = await service.apply(pull(source))
    assert replay.applied == 0 and replay.duplicates == 1
    assert len(repository.stock_rows) == len(repository.inbox) == repository.savepoints == 1
    assert not repository.projections


@pytest.mark.parametrize(
    "field",
    [
        "missing",
        "tenant_id",
        "branch_id",
        "target_edge_node_id",
        "sequence",
        "event_fingerprint",
        "previous_source_checksum",
    ],
)
async def test_missing_or_wrong_owner_anchor_never_advances_cursor(field: str) -> None:
    source = event()
    repository = StockRepository(source)
    row = repository.stock_anchor
    assert row is not None
    if field == "missing":
        repository.stock_anchor = None
    else:
        current = getattr(row, field)
        value: object = (
            UUID(int=999) if isinstance(current, UUID) else 99 if type(current) is int else "f" * 64
        )
        setattr(row, field, value)
    refused = await receiver(repository).apply(pull(source))
    assert refused.status == "quarantined" and refused.applied == 0
    assert refused.last_sequence == source.sequence - 1
    assert not repository.stock_rows and not repository.projections


@pytest.mark.parametrize("failure", ["projection", "inbox", "cursor"])
async def test_late_receive_failure_rolls_back_inbox_projection_cursor_and_allows_retry(
    failure: str,
) -> None:
    source = event()
    repository = StockRepository(source)
    repository.fail_at = failure
    with pytest.raises(RuntimeError, match="injected receive persistence failure"):
        await receiver(repository).apply(pull(source))
    assert repository.cursor.last_sequence == source.sequence - 1
    assert not repository.inbox and not repository.stock_rows and not repository.projections
    repository.fail_at = None
    applied = await receiver(repository).apply(pull(source))
    assert applied.applied == 1 and applied.status == "synced"


@pytest.mark.parametrize(
    "field", ["source_id", "source_payload_hash", "payload", "sequence", "projection_hash"]
)
async def test_warm_verifier_rejects_changed_projection_even_with_saved_inbox(field: str) -> None:
    source = event()
    repository = StockRepository(source)
    service = receiver(repository)
    await service.apply(pull(source))
    row = repository.stock_rows[source.event_id]
    if field == "payload":
        value: object = {**row.payload, "source_id": str(UUID(int=999))}
    elif field == "source_id":
        value = UUID(int=999)
    elif field == "sequence":
        value = 99
    else:
        value = "0" * 64
    setattr(row, field, value)
    result = await service.verify_projection(
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
    )
    assert result.status == "mismatch"
    assert not repository.projections
