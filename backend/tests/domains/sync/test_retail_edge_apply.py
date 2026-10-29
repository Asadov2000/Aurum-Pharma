"""Trusted retail receive/replay rules without a database or application runtime."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from app.domains.pos.retail_sale_events import (
    RetailSaleStreamPosition,
    build_retail_sale_event,
    retail_sale_event_fingerprint,
)
from app.domains.pos.schemas import SaleCheckoutResult, SaleRefundResult
from app.domains.sync.integrity import (
    canonical_json_hash,
    projection_stream_checksum,
    sale_projection_hash,
    source_stream_checksum,
)
from app.domains.sync.models import (
    SyncCursor,
    SyncInboxEvent,
    SyncPhysicalStockAnchor,
    SyncPhysicalStockProjection,
    SyncRetailEventAnchor,
    SyncSaleProjection,
)
from app.domains.sync.repository import SyncEdgeRepository
from app.domains.sync.schemas import (
    SyncEventEnvelope,
    SyncPullResponse,
    SyncQuarantineIncidentRequest,
)
from app.domains.sync.service import SyncEdgeApplyService
from tests.domains.pos.test_retail_sale_events import (
    PROJECTION,
    SOURCE,
    Kind,
    event,
    rehash,
    result,
)

NODE = UUID(int=91)


def protected_anchor(
    source: SyncEventEnvelope,
    *,
    previous_source: str = SOURCE,
    previous_projection: str = PROJECTION,
) -> SyncRetailEventAnchor:
    return SyncRetailEventAnchor(
        event_id=source.event_id,
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        target_edge_node_id=NODE,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
        sequence=source.sequence,
        previous_source_checksum=previous_source,
        previous_projection_checksum=previous_projection,
        event_fingerprint=retail_sale_event_fingerprint(
            source,
            previous_source_checksum=previous_source,
            previous_projection_checksum=previous_projection,
        ),
    )


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


class Repository:
    """Only the receive-side persistence API; snapshots model savepoint rollback."""

    def __init__(self, source: SyncEventEnvelope) -> None:
        self.cursor = SyncCursor(
            tenant_id=source.tenant_id,
            branch_id=source.branch_id,
            origin_node_id=source.origin_node_id,
            writer_epoch=source.writer_epoch,
            start_sequence=source.sequence - 1,
            start_source_checksum=SOURCE,
            start_projection_checksum=PROJECTION,
            last_sequence=source.sequence - 1,
            last_event_id=None,
            source_checksum=SOURCE,
            projection_checksum=PROJECTION,
            status="synced",
        )
        self.anchor: SyncRetailEventAnchor | None = protected_anchor(source)
        self.anchors: dict[UUID, SyncRetailEventAnchor] = {}
        self.inbox: dict[UUID, SyncInboxEvent] = {}
        self.projections: dict[UUID, SyncSaleProjection] = {}
        self.incident: SyncQuarantineIncidentRequest | None = None
        self.fail_at: str | None = None
        self.savepoints = 0
        self.anchor_lookups: list[dict[str, object]] = []

    async def get_cursor(self, **scope: object) -> SyncCursor:
        assert scope["tenant_id"] == self.cursor.tenant_id
        assert scope["branch_id"] == self.cursor.branch_id
        assert scope["for_update"] is True
        return self.cursor

    async def get_local_edge_node_id(self) -> UUID:
        return NODE

    async def get_physical_stock_anchor(self, **scope: object) -> SyncPhysicalStockAnchor | None:
        return None

    async def list_physical_stock_projections(
        self, **scope: object
    ) -> list[SyncPhysicalStockProjection]:
        return []

    async def get_retail_event_anchor(self, **scope: object) -> SyncRetailEventAnchor | None:
        self.anchor_lookups.append(scope)
        anchor = self.anchors.get(cast(UUID, scope["event_id"])) if self.anchors else self.anchor
        if anchor is None:
            return None
        expected = {
            "event_id": anchor.event_id,
            "tenant_id": anchor.tenant_id,
            "branch_id": anchor.branch_id,
            "edge_node_id": anchor.target_edge_node_id,
        }
        return anchor if scope == expected else None

    async def get_retail_event_anchor_by_position(
        self, **scope: object
    ) -> SyncRetailEventAnchor | None:
        candidates = list(self.anchors.values()) if self.anchors else [self.anchor]
        return next(
            (
                anchor
                for anchor in candidates
                if anchor is not None
                and scope
                == {
                    "tenant_id": anchor.tenant_id,
                    "branch_id": anchor.branch_id,
                    "edge_node_id": anchor.target_edge_node_id,
                    "origin_node_id": anchor.origin_node_id,
                    "writer_epoch": anchor.writer_epoch,
                    "sequence": anchor.sequence,
                }
            ),
            None,
        )

    async def get_inbox_event(self, event_id: UUID) -> SyncInboxEvent | None:
        return self.inbox.get(event_id)

    async def insert_inbox(self, source: SyncEventEnvelope, **state: object) -> SyncInboxEvent:
        record = SyncInboxEvent(**source.model_dump(mode="python"), **state)
        self.inbox[source.event_id] = record
        return record

    async def mark_inbox(self, record: SyncInboxEvent, **state: object) -> SyncInboxEvent:
        for key, value in state.items():
            setattr(record, key, value)
        if self.fail_at == "inbox":
            raise RuntimeError("injected receive persistence failure")
        return record

    async def get_sale_projection(self, sale_id: UUID) -> SyncSaleProjection | None:
        return self.projections.get(sale_id)

    async def get_sale_projection_by_operation_id(
        self, *, tenant_id: UUID, operation_id: UUID
    ) -> SyncSaleProjection | None:
        return next(
            (
                row
                for row in self.projections.values()
                if row.tenant_id == tenant_id and row.operation_id == operation_id
            ),
            None,
        )

    async def insert_sale_projection(self, **values: object) -> SyncSaleProjection:
        row = SyncSaleProjection(**values)
        self.projections[row.sale_id] = row
        if self.fail_at == "projection":
            raise RuntimeError("injected receive persistence failure")
        return row

    async def update_cursor(self, cursor: SyncCursor, **values: object) -> SyncCursor:
        for key, value in values.items():
            setattr(cursor, key, value)
        if self.fail_at == "cursor":
            raise RuntimeError("injected receive persistence failure")
        return cursor

    async def record_quarantine_incident(
        self, *, payload: SyncQuarantineIncidentRequest, **scope: object
    ) -> SyncQuarantineIncidentRequest:
        assert scope["edge_node_id"] == NODE
        self.incident = payload
        return payload

    async def get_latest_quarantine_incident(
        self, **scope: object
    ) -> SyncQuarantineIncidentRequest | None:
        return self.incident

    async def list_sale_projections(self, **scope: object) -> list[SyncSaleProjection]:
        return sorted(
            (
                row
                for row in self.projections.values()
                if row.sequence > cast(int, scope["after_sequence"])
            ),
            key=lambda row: row.sequence,
        )

    async def list_refund_projections(self, **scope: object) -> list[SyncSaleProjection]:
        return [
            row
            for row in self.projections.values()
            if row.parent_sale_id == scope["parent_sale_id"]
        ]

    @asynccontextmanager
    async def event_savepoint(self) -> AsyncIterator[None]:
        self.savepoints += 1
        cursor_state = {
            column.name: getattr(self.cursor, column.name)
            for column in SyncCursor.__table__.columns
        }
        before_inbox, before_projections = dict(self.inbox), dict(self.projections)
        try:
            yield
        except Exception:
            for key, value in cursor_state.items():
                setattr(self.cursor, key, value)
            self.inbox, self.projections = before_inbox, before_projections
            raise


def service(repository: Repository) -> SyncEdgeApplyService:
    return SyncEdgeApplyService(cast(SyncEdgeRepository, repository))


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_trusted_retail_apply_rebuilds_exact_historical_result_and_replays_once(
    kind: Kind,
) -> None:
    source = event(kind)
    repository = Repository(source)
    receiver = service(repository)
    applied = await receiver.apply(pull(source))
    assert applied.status == "synced" and applied.applied == 1
    assert repository.cursor.last_sequence == source.sequence
    assert applied.source_checksum == source.stream_checksum
    assert applied.projection_checksum == source.projection_checksum
    projection = repository.projections[source.aggregate_id]
    assert projection.schema_version == source.schema_version
    assert projection.stock_command_id == result(kind).stock_command_id
    assert receiver._projection_payload(projection) == result(kind).model_dump(mode="json")
    assert repository.inbox[source.event_id].status == "applied"
    verified = await receiver.verify_projection(
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
    )
    assert verified.status == "synced"
    replay = await receiver.apply(pull(source))
    assert replay.status == "synced" and replay.applied == 0 and replay.duplicates == 1
    assert len(repository.inbox) == len(repository.projections) == 1
    assert repository.savepoints == 1
    assert repository.anchor_lookups[0] == {
        "event_id": source.event_id,
        "tenant_id": source.tenant_id,
        "branch_id": source.branch_id,
        "edge_node_id": NODE,
    }


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "target_edge_node_id",
        "tenant_id",
        "branch_id",
        "origin_node_id",
        "writer_epoch",
        "sequence",
        "previous_source_checksum",
        "previous_projection_checksum",
        "event_fingerprint",
    ],
)
async def test_missing_or_wrong_independent_anchor_never_advances_watermark(change: str) -> None:
    source = event()
    repository = Repository(source)
    if change == "missing":
        repository.anchor = None
    else:
        assert repository.anchor is not None
        current = getattr(repository.anchor, change)
        replacement: object = (
            UUID(int=999)
            if isinstance(current, UUID)
            else current + 1 if type(current) is int else "f" * 64
        )
        setattr(repository.anchor, change, replacement)
    refused = await service(repository).apply(pull(source))
    assert refused.status == "quarantined" and refused.applied == 0
    assert refused.last_sequence == source.sequence - 1
    assert refused.source_checksum == SOURCE and refused.projection_checksum == PROJECTION
    assert not repository.projections
    assert repository.incident is not None


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_rehashed_tampering_cannot_replace_protected_anchor(kind: Kind) -> None:
    source = event(kind)
    repository = Repository(source)
    payload = deepcopy(source.payload)
    payload["receipt_number"] = "forged historical label"
    forged = rehash(source.model_copy(update={"payload": payload}))
    refused = await service(repository).apply(pull(forged))
    assert refused.status == "quarantined" and refused.last_sequence == source.sequence - 1
    assert not repository.projections


@pytest.mark.parametrize("field", ["payload", "event_type", "schema_version", "operation_id"])
async def test_duplicate_requires_full_event_identity_not_only_copied_hashes(field: str) -> None:
    source = event()
    repository = Repository(source)
    receiver = service(repository)
    await receiver.apply(pull(source))
    payload = deepcopy(source.payload)
    payload["receipt_number"] = "forged replay"
    replacement: object = {
        "payload": payload,
        "event_type": "pos.sale.refunded.v1",
        "schema_version": 99,
        "operation_id": UUID(int=999),
    }[field]
    refused = await receiver.apply(pull(source.model_copy(update={field: replacement})))
    assert refused.status == "quarantined" and refused.duplicates == 0
    assert refused.last_sequence == source.sequence
    assert len(repository.projections) == 1
    assert (
        receiver._projection_payload(repository.projections[source.aggregate_id]) == source.payload
    )


@pytest.mark.parametrize("failure", ["projection", "inbox", "cursor"])
async def test_late_failure_rolls_back_entire_current_event_before_retry(failure: str) -> None:
    source = event()
    repository = Repository(source)
    repository.fail_at = failure
    with pytest.raises(RuntimeError, match="injected receive persistence failure"):
        await service(repository).apply(pull(source))
    assert not repository.inbox and not repository.projections
    assert repository.cursor.last_sequence == source.sequence - 1
    assert repository.cursor.source_checksum == SOURCE
    assert repository.cursor.projection_checksum == PROJECTION
    repository.fail_at = None
    recovered = await service(repository).apply(pull(source))
    assert recovered.status == "synced" and recovered.applied == 1


@pytest.mark.parametrize("field", ["items", "schema_version", "stock_command_id"])
async def test_verify_projection_reports_corrupt_retail_rows_without_validation_crash(
    field: str,
) -> None:
    source = event()
    repository = Repository(source)
    receiver = service(repository)
    await receiver.apply(pull(source))
    projection = repository.projections[source.aggregate_id]
    replacement: object = {
        "items": [{"invalid": True}],
        "schema_version": 99,
        "stock_command_id": None,
    }[field]
    setattr(projection, field, replacement)
    verified = await receiver.verify_projection(
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
    )
    assert verified.status == "mismatch"
    assert verified.last_sequence == source.sequence


@pytest.mark.parametrize("bad_value", [float("nan"), object()])
async def test_malformed_duplicate_json_quarantines_instead_of_crashing(bad_value: object) -> None:
    source = event()
    repository = Repository(source)
    receiver = service(repository)
    await receiver.apply(pull(source))
    payload = deepcopy(source.payload)
    payload["receipt_number"] = bad_value
    refused = await receiver.apply(pull(source.model_copy(update={"payload": payload})))
    assert refused.status == "quarantined" and refused.duplicates == 0
    assert refused.last_sequence == source.sequence
    assert len(repository.projections) == 1


def legacy_payload(identifier: int) -> dict[str, object]:
    source = result("physical")
    return SaleCheckoutResult(
        event_id=UUID(int=identifier),
        sale_id=UUID(int=identifier + 1),
        operation_id=UUID(int=identifier + 2),
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        register_id=source.register_id,
        shift_id=source.shift_id,
        cashier_user_id=source.cashier_id,
        receipt_number=f"legacy-{identifier}",
        receipt_seq=identifier,
        created_at=source.created_at,
        completed_at=source.completed_at,
        total_amount=Decimal("0.00"),
        currency="TJS",
        is_test=False,
        items=[],
        payments=[],
    ).model_dump(mode="json")


def legacy_event(
    payload: dict[str, object],
    *,
    sequence: int,
    previous_source: str,
    previous_projection: str,
    event_type: str = "pos.sale.completed.v1",
) -> SyncEventEnvelope:
    reference = event()
    digest = canonical_json_hash(payload)
    projection = sale_projection_hash(payload)
    identifiers = {
        "event_id": UUID(str(payload["event_id"])),
        "tenant_id": reference.tenant_id,
        "branch_id": reference.branch_id,
        "origin_node_id": reference.origin_node_id,
        "writer_epoch": reference.writer_epoch,
        "sequence": sequence,
        "operation_id": UUID(str(payload["operation_id"])),
        "aggregate_type": "sale",
        "aggregate_id": UUID(str(payload["sale_id"])),
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": reference.occurred_at,
    }
    return SyncEventEnvelope(
        **identifiers,
        payload=payload,
        payload_hash=digest,
        projection_hash=projection,
        stream_checksum=source_stream_checksum(
            previous_checksum=previous_source, **identifiers, payload_hash=digest
        ),
        projection_checksum=projection_stream_checksum(
            previous_checksum=previous_projection,
            origin_node_id=reference.origin_node_id,
            writer_epoch=reference.writer_epoch,
            sequence=sequence,
            sale_id=UUID(str(payload["sale_id"])),
            projection_hash=projection,
        ),
    )


async def test_v1_v3_v4_v1_share_one_ordered_projection_and_both_checksum_chains() -> None:
    first = legacy_event(
        legacy_payload(100), sequence=3, previous_source=SOURCE, previous_projection=PROJECTION
    )
    envelopes = [first]
    repository = Repository(event())
    for index, kind in enumerate(("physical", "mixed"), start=4):
        source = result(cast(Kind, kind)).model_copy(
            update={
                "event_id": UUID(int=index * 100),
                "sale_id": UUID(int=index * 100 + 1),
                "operation_id": UUID(int=index * 100 + 2),
                "stock_command_id": UUID(int=index * 100 + 3),
            }
        )
        previous = envelopes[-1]
        position = RetailSaleStreamPosition(
            origin_node_id=previous.origin_node_id,
            writer_epoch=previous.writer_epoch,
            sequence=index,
            previous_source_checksum=previous.stream_checksum,
            previous_projection_checksum=previous.projection_checksum,
        )
        envelope = build_retail_sale_event(source, position=position)
        anchor = protected_anchor(
            envelope,
            previous_source=previous.stream_checksum,
            previous_projection=previous.projection_checksum,
        )
        repository.anchors[envelope.event_id] = anchor
        envelopes.append(envelope)
    envelopes.append(
        legacy_event(
            legacy_payload(600),
            sequence=6,
            previous_source=envelopes[-1].stream_checksum,
            previous_projection=envelopes[-1].projection_checksum,
        )
    )
    batch = pull(first).model_copy(update={"events": envelopes, "cloud_last_sequence": 6})
    receiver = service(repository)
    applied = await receiver.apply(batch)
    assert applied.status == "synced" and applied.applied == 4
    assert [row.schema_version for row in repository.projections.values()] == [1, 3, 4, 1]
    assert applied.source_checksum == envelopes[-1].stream_checksum
    assert applied.projection_checksum == envelopes[-1].projection_checksum
    for envelope in envelopes:
        assert (
            receiver._projection_payload(repository.projections[envelope.aggregate_id])
            == envelope.payload
        )
    verified = await receiver.verify_projection(
        tenant_id=first.tenant_id,
        branch_id=first.branch_id,
        origin_node_id=first.origin_node_id,
        writer_epoch=first.writer_epoch,
    )
    assert verified.status == "synced" and verified.last_sequence == 6
    replay = await receiver.apply(batch)
    assert replay.applied == 0 and replay.duplicates == 4


async def test_legacy_refund_cannot_reinterpret_a_new_physical_parent() -> None:
    source = event("physical")
    repository = Repository(source)
    receiver = service(repository)
    await receiver.apply(pull(source))
    payload = legacy_payload(700)
    payload.update(
        parent_sale_id=source.aggregate_id,
        parent_fully_refunded=True,
        total_amount="1.00",
        items=[
            {
                "id": UUID(int=710),
                "parent_sale_item_id": UUID(int=20),
                "catalog_id": UUID(int=21),
                "batch_id": UUID(int=711),
                "qty": "1.000",
                "unit_price": "1.00",
                "total_price": "1.00",
                "discount_amount": "0.00",
                "currency": "TJS",
                "position": 1,
            }
        ],
        payments=[
            {"id": UUID(int=712), "payment_method": "cash", "amount": "1.00", "currency": "TJS"}
        ],
    )
    payload = SaleRefundResult.model_validate(payload).model_dump(mode="json")
    returned = legacy_event(
        payload,
        sequence=4,
        previous_source=source.stream_checksum,
        previous_projection=source.projection_checksum,
        event_type="pos.sale.refunded.v1",
    )
    response = pull(source).model_copy(
        update={
            "events": [returned],
            "effective_after_sequence": source.sequence,
            "after_source_checksum": source.stream_checksum,
            "after_projection_checksum": source.projection_checksum,
            "cloud_last_sequence": 4,
        }
    )
    refused = await receiver.apply(response)
    assert refused.status == "quarantined" and refused.last_sequence == source.sequence
    assert len(repository.projections) == 1


@pytest.mark.parametrize("forgery", ["projection", "version", "inbox", "source_cursor"])
async def test_self_consistent_local_rehash_cannot_bypass_independent_anchor(forgery: str) -> None:
    source = event()
    repository = Repository(source)
    receiver = service(repository)
    await receiver.apply(pull(source))
    projection = repository.projections[source.aggregate_id]
    if forgery == "source_cursor":
        repository.cursor.source_checksum = "f" * 64
    else:
        projection.receipt_number = "rewritten local history"
        if forgery == "version":
            projection.schema_version = 1
            projection.stock_command_id = None
            projection.items = []
            projection.payments = []
        if forgery == "inbox":
            payload = deepcopy(source.payload)
            payload["receipt_number"] = projection.receipt_number
            forged = rehash(source.model_copy(update={"payload": payload}))
            record = repository.inbox[source.event_id]
            for field, value in forged.model_dump(mode="python").items():
                setattr(record, field, value)
            projection.source_payload_hash = forged.payload_hash
            repository.cursor.source_checksum = forged.stream_checksum
        projection.projection_hash = sale_projection_hash(receiver._projection_payload(projection))
        repository.cursor.projection_checksum = projection_stream_checksum(
            previous_checksum=PROJECTION,
            origin_node_id=source.origin_node_id,
            writer_epoch=source.writer_epoch,
            sequence=source.sequence,
            sale_id=source.aggregate_id,
            projection_hash=projection.projection_hash,
        )
    verified = await receiver.verify_projection(
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
    )
    assert verified.status == "mismatch" and verified.last_sequence == source.sequence


async def test_fresh_receiver_verifies_persisted_retail_with_trusted_local_node_scope() -> None:
    source = event()
    repository = Repository(source)
    await service(repository).apply(pull(source))
    verified = await service(repository).verify_projection(
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
    )
    assert verified.status == "synced" and verified.last_sequence == source.sequence


@pytest.mark.parametrize("rewrite_event_id", [False, True])
async def test_rewritten_v1_inbox_and_projection_cannot_hide_a_protected_retail_slot(
    rewrite_event_id: bool,
) -> None:
    source = event()
    repository = Repository(source)
    receiver = service(repository)
    await receiver.apply(pull(source))
    row = repository.projections[source.aggregate_id]
    row.schema_version, row.stock_command_id, row.items, row.payments = 1, None, [], []
    if rewrite_event_id:
        row.source_event_id = UUID(int=991)
    payload = receiver._projection_payload(row)
    forged = legacy_event(
        payload, sequence=source.sequence, previous_source=SOURCE, previous_projection=PROJECTION
    )
    del repository.inbox[source.event_id]
    repository.inbox[forged.event_id] = SyncInboxEvent(
        **forged.model_dump(mode="python"),
        status="applied",
    )
    row.source_payload_hash = forged.payload_hash
    row.projection_hash = forged.projection_hash
    repository.cursor.last_event_id = forged.event_id
    repository.cursor.source_checksum = forged.stream_checksum
    repository.cursor.projection_checksum = forged.projection_checksum
    verified = await receiver.verify_projection(
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        origin_node_id=source.origin_node_id,
        writer_epoch=source.writer_epoch,
    )
    assert verified.status == "mismatch"


async def test_untrusted_v1_envelope_cannot_occupy_a_protected_retail_position() -> None:
    source = event()
    repository = Repository(source)
    forged = legacy_event(
        legacy_payload(800),
        sequence=source.sequence,
        previous_source=SOURCE,
        previous_projection=PROJECTION,
    )
    refused = await service(repository).apply(pull(forged))
    assert refused.status == "quarantined" and refused.last_sequence == source.sequence - 1
    assert not repository.projections
