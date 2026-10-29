"""Real inventory receive writes only inbox, read projection and cursor on test PostgreSQL."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.sync.models import SyncPhysicalStockProjection
from app.domains.sync.repository import SyncEdgeRepository
from app.domains.sync.schemas import SyncPullResponse
from app.domains.sync.service import SyncEdgeApplyService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_physical_writeoff_posting import post as post_writeoff
from tests.domains.inventory.test_physical_writeoff_posting import selection
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    cart_line,
    dispense,
    receive_source,
)
from tests.domains.inventory.test_unit_physical_blocking import set_blocked
from tests.domains.pos.test_retail_checkout_source_integration import protected_facts
from tests.domains.sync.test_physical_stock_events_integration import (
    Captured,
    app_context,
    captured,
    enable_stock_gate,
    finish_sources,
)
from tests.domains.sync.test_retail_edge_apply_integration import enroll_target

db_session = receiving_session_fixture
SourceKind = Literal["dispense", "writeoff", "block", "catalog_config"]


@dataclass(frozen=True)
class ReceiveCase:
    source: Captured
    node_id: UUID

    def pull(self) -> SyncPullResponse:
        event, anchor = self.source.event, self.source.anchor
        return SyncPullResponse(
            edge_node_id=self.node_id,
            tenant_id=event.tenant_id,
            branch_id=event.branch_id,
            origin_node_id=event.origin_node_id,
            writer_epoch=event.writer_epoch,
            effective_after_sequence=event.sequence - 1,
            after_source_checksum=anchor.previous_source_checksum,
            after_projection_checksum=anchor.previous_projection_checksum,
            cloud_last_sequence=event.sequence,
            events=[event],
            has_more=False,
        )


async def edge_context(session: AsyncSession, case: ReceiveCase) -> None:
    await app_context(session)
    await session.execute(
        text("SELECT set_config('app.edge_node_id',:node,true)"),
        {"node": str(case.node_id)},
    )


async def prepared_case(
    session: AsyncSession,
    maintenance_engine: AsyncEngine,
    kind: SourceKind = "dispense",
    *,
    trust: str = "valid",
) -> ReceiveCase:
    stock = await receive_source(session)
    await session.commit()
    await enable_stock_gate(session, maintenance_engine)
    await app_context(session)
    if kind == "dispense":
        await dispense(session, stock, (cart_line(stock, "unit", 1),))
    elif kind == "writeoff":
        await post_writeoff(session, stock, selection(closed=1))
    elif kind == "block":
        await set_blocked(session, stock, True)
    else:
        await session.execute(
            text("UPDATE tenant_catalog SET base_price=77 WHERE id=:id"),
            {"id": stock.incoming.stock.catalog_id},
        )
    await finish_sources(session)
    rows = await captured(session)
    assert len(rows) == 1
    source = rows[0]
    await session.commit()
    node_id = await enroll_target(
        maintenance_engine, tenant_id=source.event.tenant_id, branch_id=source.event.branch_id
    )
    if trust != "missing":
        async with maintenance_engine.begin() as owner:
            assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await owner.scalar(text("SELECT current_user")) == "aurum_schema_owner"
            await owner.execute(
                text(
                    "INSERT INTO sync_physical_stock_anchor(event_id,tenant_id,branch_id,"
                    "target_edge_node_id,origin_node_id,writer_epoch,sequence,"
                    "previous_source_checksum,previous_projection_checksum,event_fingerprint) "
                    "SELECT id,tenant_id,branch_id,:node,origin_node_id,writer_epoch,sequence,"
                    "previous_source_checksum,previous_projection_checksum,event_fingerprint "
                    "FROM sync_physical_stock_event WHERE tenant_id=:tenant AND id=:event"
                ),
                {
                    "node": uuid4() if trust == "wrong-node" else node_id,
                    "tenant": source.event.tenant_id,
                    "event": source.event.event_id,
                },
            )
    case = ReceiveCase(source, node_id)
    await edge_context(session, case)
    return case


@pytest.mark.parametrize("kind", ["dispense", "writeoff", "block", "catalog_config"])
async def test_real_receive_replay_and_verification_never_repeat_source_effects(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    kind: SourceKind,
) -> None:
    case = await prepared_case(db_session, maintenance_engine, kind)
    before = await protected_facts(db_session)
    receiver = SyncEdgeApplyService(SyncEdgeRepository(db_session))
    applied = await receiver.apply(case.pull())
    assert applied.status == "synced" and applied.applied == 1
    projection = await db_session.get(SyncPhysicalStockProjection, case.source.event.event_id)
    assert projection is not None and projection.payload == case.source.event.payload
    verified = await receiver.verify_projection(
        tenant_id=case.source.event.tenant_id,
        branch_id=case.source.event.branch_id,
        origin_node_id=case.source.event.origin_node_id,
        writer_epoch=case.source.event.writer_epoch,
    )
    assert verified.status == "synced"
    await db_session.commit()
    await edge_context(db_session, case)
    replay = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(case.pull())
    assert replay.applied == 0 and replay.duplicates == 1
    assert await protected_facts(db_session) == before
    assert len(await captured(db_session)) == 1


@pytest.mark.parametrize("trust", ["missing", "wrong-node"])
async def test_real_receive_cannot_infer_trust_from_valid_source_hashes(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    trust: str,
) -> None:
    case = await prepared_case(db_session, maintenance_engine, trust=trust)
    refused = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(case.pull())
    assert refused.status == "quarantined" and refused.applied == 0
    assert refused.last_sequence == case.source.event.sequence - 1
    assert await db_session.get(SyncPhysicalStockProjection, case.source.event.event_id) is None


@pytest.mark.parametrize("failure", ["projection", "inbox", "cursor"])
async def test_real_late_failure_removes_inbox_projection_and_cursor_before_clean_retry(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    case = await prepared_case(db_session, maintenance_engine)
    before = await protected_facts(db_session)
    repository = SyncEdgeRepository(db_session)
    method = {
        "projection": "insert_physical_stock_projection",
        "inbox": "mark_inbox",
        "cursor": "update_cursor",
    }[failure]
    original = getattr(repository, method)

    async def fail_after_write(*arguments: object, **values: object) -> object:
        await original(*arguments, **values)
        raise RuntimeError("injected inventory receive failure")

    with monkeypatch.context() as patch:
        patch.setattr(repository, method, fail_after_write)
        with pytest.raises(RuntimeError, match="injected inventory receive failure"):
            await SyncEdgeApplyService(repository).apply(case.pull())
    event = case.source.event
    assert await repository.get_inbox_event(event.event_id) is None
    assert await repository.get_physical_stock_projection(event.event_id) is None
    cursor = await repository.get_cursor(
        tenant_id=event.tenant_id,
        branch_id=event.branch_id,
        origin_node_id=event.origin_node_id,
        writer_epoch=event.writer_epoch,
    )
    assert cursor is not None and cursor.last_sequence == event.sequence - 1
    assert await protected_facts(db_session) == before
    result = await SyncEdgeApplyService(repository).apply(case.pull())
    assert result.status == "synced" and result.applied == 1
