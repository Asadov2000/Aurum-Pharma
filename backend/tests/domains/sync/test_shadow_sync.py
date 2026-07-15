"""Shadow replication invariants on the real PostgreSQL schema."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.time import utc_now
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.sync.bootstrap import (
    BootstrapScope,
    BootstrapValidationError,
    chunk_as_pull,
    verify_chunk,
    verify_manifest,
)
from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.integrity import ZERO_CHECKSUM
from app.domains.sync.models import SyncInboxEvent, SyncSaleProjection
from app.domains.sync.repository import (
    SyncCloudRepository,
    SyncEdgeRepository,
    SyncOutboxRepository,
)
from app.domains.sync.schemas import SyncNodeCreate, SyncShadowReportRequest
from app.domains.sync.service import SyncAdminService, SyncCloudService, SyncEdgeApplyService


async def _open_shift(service: POSService, scaffold) -> None:  # type: ignore[no-untyped-def]
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )


async def _checkout(service: POSService, scaffold):  # type: ignore[no-untyped-def]
    return await service.checkout(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=uuid4(),
        items=[(scaffold["item"].id, Decimal("1"))],
        payments=[("cash", Decimal("10"), None)],
    )


async def _enroll(session: AsyncSession, scaffold):  # type: ignore[no-untyped-def]
    return await SyncAdminService(SyncCloudRepository(session)).create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Edge test",
        )
    )


async def _pull(session: AsyncSession, node, *, after: int = 0):  # type: ignore[no-untyped-def]
    return await SyncCloudService(SyncCloudRepository(session)).pull(
        edge_node_id=node.id,
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        shadow_start_sequence=node.shadow_start_sequence,
        shadow_start_checksum=node.shadow_start_checksum,
        shadow_start_projection_checksum=node.shadow_start_projection_checksum,
        shadow_start_origin_node_id=node.shadow_start_origin_node_id,
        shadow_start_writer_epoch=node.shadow_start_writer_epoch,
        after_sequence=after,
        limit=100,
    )


async def test_shadow_sale_matches_cloud_checkpoint(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    pos = POSService(POSRepository(db_session))
    await _open_shift(pos, scaffold)
    node = await _enroll(db_session, scaffold)
    sale = await _checkout(pos, scaffold)

    pull = await _pull(db_session, node)
    assert [event.sequence for event in pull.events] == [1]
    assert pull.events[0].event_id == sale.event_id

    edge = SyncEdgeApplyService(SyncEdgeRepository(db_session))
    applied = await edge.apply(pull)
    assert applied.status == "synced"
    assert applied.applied == 1
    verified = await edge.verify_projection(
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        origin_node_id=pull.origin_node_id,
        writer_epoch=pull.writer_epoch,
    )
    assert verified.status == "synced"

    report = await SyncCloudService(SyncCloudRepository(db_session)).report(
        edge_node_id=node.id,
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        shadow_start_sequence=node.shadow_start_sequence,
        shadow_start_checksum=node.shadow_start_checksum,
        shadow_start_projection_checksum=node.shadow_start_projection_checksum,
        payload=SyncShadowReportRequest(
            report_id=uuid4(),
            origin_node_id=pull.origin_node_id,
            writer_epoch=pull.writer_epoch,
            last_sequence=verified.last_sequence,
            source_checksum=verified.source_checksum,
            projection_checksum=verified.projection_checksum,
        ),
    )
    assert report.status == "matched"
    assert report.source_verified is True
    assert report.expected_source_checksum == pull.events[0].stream_checksum
    assert report.expected_checksum == pull.events[0].projection_checksum


async def test_shadow_report_rejects_source_chain_mismatch(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    pos = POSService(POSRepository(db_session))
    await _open_shift(pos, scaffold)
    node = await _enroll(db_session, scaffold)
    await _checkout(pos, scaffold)
    pull = await _pull(db_session, node)
    edge = SyncEdgeApplyService(SyncEdgeRepository(db_session))
    verified = await edge.apply(pull)

    report = await SyncCloudService(SyncCloudRepository(db_session)).report(
        edge_node_id=node.id,
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        shadow_start_sequence=node.shadow_start_sequence,
        shadow_start_checksum=node.shadow_start_checksum,
        shadow_start_projection_checksum=node.shadow_start_projection_checksum,
        payload=SyncShadowReportRequest(
            report_id=uuid4(),
            origin_node_id=pull.origin_node_id,
            writer_epoch=pull.writer_epoch,
            last_sequence=verified.last_sequence,
            source_checksum="f" * 64,
            projection_checksum=verified.projection_checksum,
        ),
    )

    assert report.status == "mismatch"
    assert report.source_verified is False
    assert report.expected_source_checksum == verified.source_checksum


async def test_lost_ack_replay_is_a_verified_duplicate(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    pos = POSService(POSRepository(db_session))
    await _open_shift(pos, scaffold)
    node = await _enroll(db_session, scaffold)
    await _checkout(pos, scaffold)
    pull = await _pull(db_session, node)
    edge = SyncEdgeApplyService(SyncEdgeRepository(db_session))

    first = await edge.apply(pull)
    replay = await edge.apply(pull)

    assert first.applied == 1
    assert replay.status == "synced"
    assert replay.applied == 0
    assert replay.duplicates == 1
    projection = await db_session.get(SyncSaleProjection, pull.events[0].aggregate_id)
    assert projection is not None


async def test_sequence_gap_quarantines_and_does_not_advance_cursor(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    pos = POSService(POSRepository(db_session))
    await _open_shift(pos, scaffold)
    node = await _enroll(db_session, scaffold)
    await _checkout(pos, scaffold)
    pull = await _pull(db_session, node)
    bad_event = pull.events[0].model_copy(update={"sequence": 2})
    bad_pull = pull.model_copy(update={"events": [bad_event]})

    result = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(bad_pull)

    assert result.status == "gap"
    assert result.last_sequence == 0
    inbox = await db_session.get(SyncInboxEvent, bad_event.event_id)
    assert inbox is not None
    assert inbox.status == "quarantined"
    assert inbox.reason_code == "sequence_gap"


async def test_payload_tampering_is_quarantined_before_projection(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    pos = POSService(POSRepository(db_session))
    await _open_shift(pos, scaffold)
    node = await _enroll(db_session, scaffold)
    await _checkout(pos, scaffold)
    pull = await _pull(db_session, node)
    payload = dict(pull.events[0].payload)
    payload["receipt_number"] = "tampered"
    bad_event = pull.events[0].model_copy(update={"payload": payload})
    bad_pull = pull.model_copy(update={"events": [bad_event]})

    result = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(bad_pull)

    assert result.status == "quarantined"
    inbox = await db_session.get(SyncInboxEvent, bad_event.event_id)
    assert inbox is not None
    assert inbox.reason_code == "payload_hash_mismatch"
    assert await db_session.get(SyncSaleProjection, bad_event.aggregate_id) is None


async def test_projection_is_rebuilt_from_local_rows_before_report(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    pos = POSService(POSRepository(db_session))
    await _open_shift(pos, scaffold)
    node = await _enroll(db_session, scaffold)
    await _checkout(pos, scaffold)
    pull = await _pull(db_session, node)
    edge = SyncEdgeApplyService(SyncEdgeRepository(db_session))
    await edge.apply(pull)
    projection = await db_session.get(SyncSaleProjection, pull.events[0].aggregate_id)
    assert projection is not None
    projection.receipt_number = "corrupted"
    await db_session.flush()

    verified = await edge.verify_projection(
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        origin_node_id=pull.origin_node_id,
        writer_epoch=pull.writer_epoch,
    )

    assert verified.status == "mismatch"


async def test_late_enrollment_starts_after_immutable_history(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold(batch_qty=10)
    pos = POSService(POSRepository(db_session))
    await _open_shift(pos, scaffold)
    first = await _checkout(pos, scaffold)
    historical_second = await _checkout(pos, scaffold)
    node = await _enroll(db_session, scaffold)
    live = await _checkout(pos, scaffold)

    pull = await _pull(db_session, node)

    assert node.shadow_start_sequence == 2
    assert [event.event_id for event in pull.events] == [live.event_id]
    assert first.event_id not in {event.event_id for event in pull.events}
    assert historical_second.event_id not in {event.event_id for event in pull.events}
    credential = parse_edge_credential(node.credential)
    scope = BootstrapScope(
        edge_node_id=node.id,
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        credential_issued_at=node.credential_issued_at,
        credential_expires_at=node.credential_expires_at,
        origin_node_id=node.shadow_start_origin_node_id,
        writer_epoch=node.shadow_start_writer_epoch,
        root_source_checksum=ZERO_CHECKSUM,
        root_projection_checksum=ZERO_CHECKSUM,
        checkpoint_sequence=node.shadow_start_sequence,
        source_checksum=node.shadow_start_checksum,
        projection_checksum=node.shadow_start_projection_checksum,
    )
    cloud = SyncCloudService(SyncCloudRepository(db_session))
    settings = get_settings()
    previous_chunk_size = settings.EDGE_BOOTSTRAP_CHUNK_SIZE
    try:
        settings.EDGE_BOOTSTRAP_CHUNK_SIZE = 1
        signed = await cloud.bootstrap_manifest(scope=scope)
        manifest = verify_manifest(signed, credential=node.credential, now=utc_now())
        chunks = [
            await cloud.bootstrap_chunk(
                bootstrap_id=manifest.bootstrap_id,
                chunk_index=index,
                scope=scope,
            )
            for index in range(len(manifest.chunks))
        ]
    finally:
        settings.EDGE_BOOTSTRAP_CHUNK_SIZE = previous_chunk_size

    assert manifest.checkpoint_sequence == 2
    assert len(chunks) == 2
    for chunk in chunks:
        verify_chunk(manifest, chunk)
    tampered_event = chunks[0].events[0].model_copy(update={"payload_hash": "0" * 64})
    tampered_chunk = chunks[0].model_copy(update={"events": [tampered_event]})
    with pytest.raises(BootstrapValidationError, match="chunk hash"):
        verify_chunk(manifest, tampered_chunk)

    edge = SyncEdgeApplyService(SyncEdgeRepository(db_session))
    first_chunk = await edge.apply(chunk_as_pull(manifest=manifest, chunk=chunks[0]))
    assert first_chunk.status == "synced"
    assert first_chunk.last_sequence == 1
    assert await db_session.get(SyncSaleProjection, first.sale_id) is not None
    replayed = await edge.apply(chunk_as_pull(manifest=manifest, chunk=chunks[0]))
    assert replayed.status == "synced"
    assert replayed.duplicates == 1
    assert replayed.last_sequence == 1
    resumed = await edge.apply(chunk_as_pull(manifest=manifest, chunk=chunks[1]))
    assert resumed.status == "synced"
    assert resumed.last_sequence == 2
    assert await db_session.get(SyncSaleProjection, historical_second.sale_id) is not None

    applied = await edge.apply(pull)
    assert applied.status == "synced"
    assert applied.last_sequence == 3
    verified = await edge.verify_projection(
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        origin_node_id=pull.origin_node_id,
        writer_epoch=pull.writer_epoch,
    )
    assert verified.status == "synced"


async def test_outbox_rejects_events_from_another_writer_epoch(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold(batch_qty=10)
    pos = POSService(POSRepository(db_session))
    await _open_shift(pos, scaffold)
    node = await _enroll(db_session, scaffold)
    sale = await _checkout(pos, scaffold)
    stream = await SyncCloudRepository(db_session).get_stream(
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
    )
    assert stream is not None

    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await SyncOutboxRepository(db_session).enqueue(
                event_id=uuid4(),
                tenant_id=node.tenant_id,
                branch_id=node.branch_id,
                origin_node_id=stream.writer_node_id,
                writer_epoch=stream.writer_epoch + 1,
                sequence=2,
                operation_id=uuid4(),
                aggregate_type="sale",
                aggregate_id=uuid4(),
                event_type="pos.sale.completed.v1",
                schema_version=1,
                occurred_at=utc_now(),
                payload={},
                payload_hash="a" * 64,
                stream_checksum="b" * 64,
                projection_hash="c" * 64,
                projection_checksum="d" * 64,
            )
    assert getattr(error.value.orig, "sqlstate", None) == "42501"

    pull = await _pull(db_session, node)

    assert [event.event_id for event in pull.events] == [sale.event_id]


async def test_edge_cursors_are_scoped_by_writer_epoch(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    node = await _enroll(db_session, scaffold)
    stream = await SyncCloudRepository(db_session).get_stream(
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
    )
    assert stream is not None
    repo = SyncEdgeRepository(db_session)
    first = await repo.insert_cursor(
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        origin_node_id=stream.writer_node_id,
        writer_epoch=stream.writer_epoch,
        start_sequence=0,
        start_source_checksum="0" * 64,
        start_projection_checksum="0" * 64,
    )
    second = await repo.insert_cursor(
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        origin_node_id=stream.writer_node_id,
        writer_epoch=stream.writer_epoch + 1,
        start_sequence=0,
        start_source_checksum="1" * 64,
        start_projection_checksum="2" * 64,
    )

    assert first.writer_epoch != second.writer_epoch
    assert (
        await repo.get_cursor(
            tenant_id=node.tenant_id,
            branch_id=node.branch_id,
            origin_node_id=stream.writer_node_id,
            writer_epoch=first.writer_epoch,
        )
    ) is first
    assert (
        await repo.get_cursor(
            tenant_id=node.tenant_id,
            branch_id=node.branch_id,
            origin_node_id=stream.writer_node_id,
            writer_epoch=second.writer_epoch,
        )
    ) is second
