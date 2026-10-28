"""Receive actual committed checkout events on disposable PostgreSQL CI data."""

import asyncio
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.pos.repository import POSRepository
from app.domains.pos.retail_checkout_event_service import RetailCheckoutEventCandidate
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from app.domains.pos.service import POSService
from app.domains.sync.models import SyncCursor, SyncOutboxEvent, SyncSaleProjection
from app.domains.sync.repository import SyncCloudRepository, SyncEdgeRepository
from app.domains.sync.schemas import (
    EdgeApplyResult,
    SyncEventEnvelope,
    SyncNodeCreate,
    SyncPullResponse,
)
from app.domains.sync.service import SyncAdminService, SyncEdgeApplyService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cash_checkout import complete as complete_physical
from tests.domains.pos.test_retail_checkout_event_producer_integration import enable_gate, produce
from tests.domains.pos.test_retail_checkout_origin_integration import finish, prepare, stream_facts
from tests.domains.pos.test_retail_checkout_source_integration import (
    PATHS,
    PathKind,
    protected_facts,
)
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


@dataclass(frozen=True)
class Case:
    source: RetailCheckoutResult
    candidate: RetailCheckoutEventCandidate
    envelope: SyncEventEnvelope
    node_id: UUID
    context: ReceivingRuntime

    def pull(self) -> SyncPullResponse:
        return SyncPullResponse(
            edge_node_id=self.node_id,
            tenant_id=self.source.tenant_id,
            branch_id=self.source.branch_id,
            origin_node_id=self.envelope.origin_node_id,
            writer_epoch=self.envelope.writer_epoch,
            effective_after_sequence=self.envelope.sequence - 1,
            after_source_checksum=self.candidate.anchor.previous_source_checksum,
            after_projection_checksum=self.candidate.anchor.previous_projection_checksum,
            cloud_last_sequence=self.envelope.sequence,
            events=[self.envelope],
            has_more=False,
        )


async def edge_context(session: AsyncSession, case: Case) -> None:
    await set_receiving_context(session, case.context.stock, case.context.actor_id)
    await session.execute(
        text(
            "SELECT set_config('app.branch_id',:branch,true),"
            "set_config('app.edge_node_id',:node,true)"
        ),
        {"branch": str(case.source.branch_id), "node": str(case.node_id)},
    )


async def stage_anchor(maintenance_engine: AsyncEngine, case: Case) -> None:
    anchor = case.candidate.anchor
    async with maintenance_engine.begin() as connection:
        assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        stored = (
            await connection.execute(
                text(
                    "SELECT record,event_fingerprint,previous_source_checksum,"
                    "previous_projection_checksum FROM pos_retail_checkout_event "
                    "WHERE tenant_id=:tenant AND event_id=:event"
                ),
                {"tenant": case.source.tenant_id, "event": case.envelope.event_id},
            )
        ).one()
        assert bytes(stored.record) == case.candidate.record
        assert stored.event_fingerprint == anchor.event_fingerprint
        assert stored.previous_source_checksum == anchor.previous_source_checksum
        assert stored.previous_projection_checksum == anchor.previous_projection_checksum
        await connection.execute(
            text("""
                INSERT INTO sync_retail_event_anchor(
                    event_id,tenant_id,branch_id,target_edge_node_id,origin_node_id,
                    writer_epoch,sequence,previous_source_checksum,
                    previous_projection_checksum,event_fingerprint,created_by)
                SELECT event_id,tenant_id,branch_id,:target,origin_node_id,
                    writer_epoch,sequence,previous_source_checksum,
                    previous_projection_checksum,event_fingerprint,created_by
                FROM pos_retail_checkout_event WHERE tenant_id=:tenant AND event_id=:event
            """),
            {
                "event": case.envelope.event_id,
                "tenant": anchor.tenant_id,
                "target": case.node_id,
            },
        )


async def enroll_target(
    maintenance_engine: AsyncEngine, *, tenant_id: UUID, branch_id: UUID
) -> UUID:
    """Provision a synthetic receiver as schema owner after source commit."""
    async with AsyncSession(maintenance_engine, expire_on_commit=False) as owner:
        async with owner.begin():
            assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await owner.scalar(text("SELECT current_user")) == "aurum_schema_owner"
            await owner.execute(
                text(
                    "SELECT set_config('app.tenant_id',:tenant,true),"
                    "set_config('app.branch_id',:branch,true)"
                ),
                {"tenant": str(tenant_id), "branch": str(branch_id)},
            )
            node = await SyncAdminService(SyncCloudRepository(owner)).create_node(
                SyncNodeCreate(
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    display_name="Retail receive test",
                )
            )
            return node.id


async def prepared_case(
    session: AsyncSession,
    maintenance_engine: AsyncEngine,
    path: PathKind,
    *,
    with_anchor: bool = True,
) -> Case:
    await enable_gate(session, maintenance_engine)
    source = await finish(session, await prepare(session, path))
    candidate = await produce(session, source)
    assert candidate is not None
    envelope = SyncEventEnvelope.model_validate(json.loads(candidate.record)["envelope"])
    context = runtime(session)
    await session.commit()
    node_id = await enroll_target(
        maintenance_engine, tenant_id=source.tenant_id, branch_id=source.branch_id
    )
    case = Case(source, candidate, envelope, node_id, context)
    if with_anchor:
        await stage_anchor(maintenance_engine, case)
    await edge_context(session, case)
    return case


@pytest.mark.parametrize("path", PATHS)
async def test_real_checkout_projects_and_recovers_without_financial_or_stock_effects(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, path: PathKind
) -> None:
    case = await prepared_case(db_session, maintenance_engine, path)
    before = await protected_facts(db_session)
    receiver = SyncEdgeApplyService(SyncEdgeRepository(db_session))
    applied = await receiver.apply(case.pull())
    assert applied.status == "synced" and applied.applied == 1
    projection = await db_session.get(SyncSaleProjection, case.source.sale_id)
    assert projection is not None
    assert projection.schema_version == case.source.schema_version
    assert projection.stock_command_id == case.source.stock_command_id
    assert receiver._projection_payload(projection) == case.source.model_dump(mode="json")
    verified = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).verify_projection(
        tenant_id=case.source.tenant_id,
        branch_id=case.source.branch_id,
        origin_node_id=case.envelope.origin_node_id,
        writer_epoch=case.envelope.writer_epoch,
    )
    assert verified.status == "synced"
    assert verified.projection_checksum == case.envelope.projection_checksum
    await db_session.commit()
    await edge_context(db_session, case)
    replay = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(case.pull())
    assert replay.applied == 0 and replay.duplicates == 1
    assert replay.source_checksum == case.envelope.stream_checksum
    assert await protected_facts(db_session) == before


@pytest.mark.parametrize("trust", ["missing", "wrong_target"])
async def test_real_receiver_rejects_absent_or_other_nodes_protected_anchor(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, trust: str
) -> None:
    case = await prepared_case(db_session, maintenance_engine, "mixed-cash", with_anchor=False)
    if trust == "wrong_target":
        await stage_anchor(maintenance_engine, replace(case, node_id=uuid4()))
    before = await protected_facts(db_session)
    refused = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(case.pull())
    assert refused.status == "quarantined" and refused.applied == 0
    assert refused.last_sequence == case.envelope.sequence - 1
    assert refused.source_checksum == case.candidate.anchor.previous_source_checksum
    assert refused.projection_checksum == case.candidate.anchor.previous_projection_checksum
    assert await db_session.get(SyncSaleProjection, case.source.sale_id) is None
    assert await protected_facts(db_session) == before


@pytest.mark.parametrize("failure", ["projection", "inbox", "cursor"])
async def test_database_savepoint_removes_partial_event_and_allows_original_retry(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    case = await prepared_case(db_session, maintenance_engine, "mixed-electronic")
    before = await protected_facts(db_session)
    method = {
        "projection": "insert_sale_projection",
        "inbox": "mark_inbox",
        "cursor": "update_cursor",
    }[failure]
    original = getattr(SyncEdgeRepository, method)

    async def fail_after_write(
        repository: SyncEdgeRepository, *args: object, **values: object
    ) -> None:
        await original(repository, *args, **values)
        raise RuntimeError("injected receive transaction failure")

    with monkeypatch.context() as patch:
        patch.setattr(SyncEdgeRepository, method, fail_after_write)
        with pytest.raises(RuntimeError, match="injected receive transaction failure"):
            await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(case.pull())
    for table in ("sync_inbox", "sync_sale_projection"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": case.source.tenant_id},
            )
            == 0
        )
    cursor = (
        await db_session.execute(
            text(
                "SELECT last_sequence,source_checksum,projection_checksum FROM sync_cursor "
                "WHERE tenant_id=:tenant AND branch_id=:branch"
            ),
            {"tenant": case.source.tenant_id, "branch": case.source.branch_id},
        )
    ).one()
    assert cursor == (
        case.envelope.sequence - 1,
        case.candidate.anchor.previous_source_checksum,
        case.candidate.anchor.previous_projection_checksum,
    )
    assert await protected_facts(db_session) == before
    recovered = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(case.pull())
    assert recovered.status == "synced" and recovered.applied == 1


@pytest.mark.parametrize("cursor_exists", [False, True])
async def test_concurrent_receivers_share_locked_cursor_and_apply_real_event_once(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, cursor_exists: bool
) -> None:
    case = await prepared_case(db_session, maintenance_engine, "physical-cash")
    if cursor_exists:
        empty = case.pull().model_copy(update={"events": []})
        await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(empty)
    await db_session.commit()
    barrier = asyncio.Barrier(2)

    class RacingRepository(SyncEdgeRepository):
        async def get_cursor(self, **scope: object) -> SyncCursor | None:
            cursor = await super().get_cursor(**scope)
            if not cursor_exists and cursor is None:
                await barrier.wait()
            return cursor

    async def receive() -> EdgeApplyResult:
        async with case.context.factory() as session:
            await edge_context(session, case)
            result = await SyncEdgeApplyService(RacingRepository(session)).apply(case.pull())
            await session.commit()
            return result

    first, second = await asyncio.wait_for(asyncio.gather(receive(), receive()), timeout=20)
    assert first.status == second.status == "synced"
    assert first.applied + second.applied == 1
    assert first.duplicates + second.duplicates == 1
    await edge_context(db_session, case)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM sync_sale_projection WHERE tenant_id=:tenant"),
            {"tenant": case.source.tenant_id},
        )
        == 1
    )


async def checkout_legacy(session: AsyncSession, register_id: UUID) -> None:
    context = runtime(session)
    pos = POSService(POSRepository(session))
    sale = await pos.create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=register_id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    await legacy_with_stock(session, sale)
    await pos.checkout(
        tenant_id=context.stock.tenant_id,
        register_id=register_id,
        cashier_user_id=context.actor_id,
        operation_id=uuid4(),
        draft_sale_id=sale.id,
        items=[(context.stock.catalog_id, Decimal("0.001"))],
        payments=[("cash", Decimal("0.02"), None)],
        allowed_branch_ids={context.stock.branch_id},
    )


async def test_real_v1_v3_v4_v1_stream_keeps_exact_documents_and_shared_watermark(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    await enable_gate(db_session, maintenance_engine)
    physical = await checkout_source(db_session)
    await put_physical(db_session, physical.sale.id)
    request = await saved_request(db_session, physical.sale)
    baseline = (await stream_facts(db_session))["streams"][0]
    await checkout_legacy(db_session, physical.sale.register_id)
    first = await complete_physical(db_session, request)
    second = await finish(db_session, await prepare(db_session, "mixed-cash"))
    await checkout_legacy(db_session, physical.sale.register_id)
    candidates = [await produce(db_session, source) for source in (first, second)]
    assert all(candidate is not None for candidate in candidates)
    context = runtime(db_session)
    rows = list(
        (
            await db_session.scalars(
                select(SyncOutboxEvent)
                .where(
                    SyncOutboxEvent.tenant_id == first.tenant_id,
                    SyncOutboxEvent.branch_id == first.branch_id,
                    SyncOutboxEvent.sequence > baseline.last_sequence,
                )
                .order_by(SyncOutboxEvent.sequence)
            )
        ).all()
    )
    events = [SyncEventEnvelope.model_validate(row, from_attributes=True) for row in rows]
    assert [item.schema_version for item in events] == [1, 3, 4, 1]
    await db_session.commit()
    node_id = await enroll_target(
        maintenance_engine, tenant_id=first.tenant_id, branch_id=first.branch_id
    )
    cases = []
    for source, candidate in zip((first, second), candidates, strict=True):
        assert candidate is not None
        envelope = next(item for item in events if item.event_id == source.event_id)
        case = Case(source, candidate, envelope, node_id, context)
        cases.append(case)
        await stage_anchor(maintenance_engine, case)
    await edge_context(db_session, cases[0])
    before = await protected_facts(db_session)
    batch = (
        cases[0]
        .pull()
        .model_copy(
            update={
                "effective_after_sequence": baseline.last_sequence,
                "after_source_checksum": baseline.current_checksum,
                "after_projection_checksum": baseline.current_projection_checksum,
                "events": events,
                "cloud_last_sequence": events[-1].sequence,
            }
        )
    )
    receiver = SyncEdgeApplyService(SyncEdgeRepository(db_session))
    applied = await receiver.apply(batch)
    assert applied.status == "synced" and applied.applied == 4
    for envelope in events:
        row = await db_session.get(SyncSaleProjection, envelope.aggregate_id)
        assert row is not None
        assert receiver._projection_payload(row) == envelope.payload
    verified = await receiver.verify_projection(
        tenant_id=first.tenant_id,
        branch_id=first.branch_id,
        origin_node_id=events[0].origin_node_id,
        writer_epoch=events[0].writer_epoch,
    )
    assert verified.status == "synced"
    assert verified.source_checksum == events[-1].stream_checksum
    assert verified.projection_checksum == events[-1].projection_checksum
    replay = await receiver.apply(batch)
    assert replay.applied == 0 and replay.duplicates == 4
    assert await protected_facts(db_session) == before
