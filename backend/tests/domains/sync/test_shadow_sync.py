"""Shadow replication invariants on the real PostgreSQL schema."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.sync.models import SyncInboxEvent, SyncSaleProjection
from app.domains.sync.repository import SyncCloudRepository, SyncEdgeRepository
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
    )
    assert verified.status == "synced"

    report = await SyncCloudService(SyncCloudRepository(db_session)).report(
        edge_node_id=node.id,
        tenant_id=node.tenant_id,
        branch_id=node.branch_id,
        shadow_start_sequence=node.shadow_start_sequence,
        shadow_start_projection_checksum=node.shadow_start_projection_checksum,
        payload=SyncShadowReportRequest(
            report_id=uuid4(),
            origin_node_id=pull.origin_node_id,
            writer_epoch=pull.writer_epoch,
            last_sequence=verified.last_sequence,
            projection_checksum=verified.projection_checksum,
        ),
    )
    assert report.status == "matched"
    assert report.expected_checksum == pull.events[0].projection_checksum


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
    node = await _enroll(db_session, scaffold)
    second = await _checkout(pos, scaffold)

    pull = await _pull(db_session, node)

    assert node.shadow_start_sequence == 1
    assert [event.event_id for event in pull.events] == [second.event_id]
    assert first.event_id not in {event.event_id for event in pull.events}
    applied = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(pull)
    assert applied.status == "synced"
    assert applied.last_sequence == 2
