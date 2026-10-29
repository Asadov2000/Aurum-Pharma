"""A signed physical baseline and applied PostgreSQL events form one authorized read view."""

from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.sync.physical_read_snapshot import PhysicalRecord
from app.domains.sync.physical_read_snapshot_repository import read_physical_source_facts
from app.domains.sync.physical_read_snapshot_service import (
    build_physical_source_records,
    install_physical_read_snapshot,
    read_physical_snapshot,
)
from app.domains.sync.physical_stock_event_repository import materialize_physical_stock_projection
from app.domains.sync.physical_stock_events import PhysicalStockConfig
from app.domains.sync.physical_stock_projection import (
    PhysicalStockBaselineAnchor,
    PhysicalStockProjection,
    physical_baseline_configuration_hash,
    physical_baseline_records_hash,
)
from app.domains.sync.repository import SyncEdgeRepository
from app.domains.sync.schemas import SyncPullResponse
from app.domains.sync.service import SyncEdgeApplyService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_physical_writeoff_posting import post as post_writeoff
from tests.domains.inventory.test_physical_writeoff_posting import selection
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense
from tests.domains.pos.test_retail_checkout_source_integration import protected_facts
from tests.domains.sync.test_physical_read_snapshot_integration import (
    SnapshotCase,
    prepare_snapshot_case,
    repeatable_context,
)
from tests.domains.sync.test_physical_stock_events_integration import (
    Captured,
    app_context,
    captured,
    enable_stock_gate,
    finish_sources,
)

db_session = receiving_session_fixture


@dataclass(frozen=True)
class BaselineCase:
    snapshot: SnapshotCase
    records: tuple[PhysicalRecord, ...]
    configuration: tuple[PhysicalStockConfig, ...]
    anchor: PhysicalStockBaselineAnchor


async def configuration(session: AsyncSession) -> tuple[PhysicalStockConfig, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT id AS catalog_id,physical_config_revision AS revision,"
                "base_price,unit_sale_preparation AS preparation FROM tenant_catalog ORDER BY id"
            )
        )
    ).mappings()
    return tuple(PhysicalStockConfig.model_validate(dict(row)) for row in rows)


async def scoped_context(session: AsyncSession, case: BaselineCase) -> None:
    await app_context(session)
    await session.execute(
        text("SELECT set_config('app.edge_node_id',:node,true)"),
        {"node": str(case.anchor.target_edge_node_id)},
    )


async def trusted_baseline(session: AsyncSession, maintenance_engine: AsyncEngine) -> BaselineCase:
    await enable_stock_gate(session, maintenance_engine)
    await app_context(session)
    snapshot = await prepare_snapshot_case(session, maintenance_engine)
    repo = SyncEdgeRepository(session)
    await install_physical_read_snapshot(
        repo,
        envelope=snapshot.envelope,
        chunks=snapshot.chunks,
        credential=snapshot.credential.token,
    )
    context = snapshot.runtime
    records = await read_physical_snapshot(
        repo,
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=context.actor_id,
        credential=snapshot.credential.token,
    )
    configs = await configuration(session)
    stream = (
        (
            await session.execute(
                text(
                    "SELECT writer_node_id AS origin_node_id,writer_epoch,"
                    "last_sequence AS sequence,current_checksum AS source_checksum,"
                    "current_projection_checksum AS projection_checksum "
                    "FROM sync_stream WHERE tenant_id=:tenant AND branch_id=:branch"
                ),
                {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id},
            )
        )
        .mappings()
        .one()
    )
    anchor = PhysicalStockBaselineAnchor(
        snapshot_id=snapshot.envelope.manifest.snapshot_id,
        snapshot_hash=snapshot.envelope.manifest.snapshot_hash,
        physical_records_hash=physical_baseline_records_hash(records),
        configuration_hash=physical_baseline_configuration_hash(configs),
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        target_edge_node_id=snapshot.envelope.manifest.edge_node_id,
        **dict(stream),
    )
    await session.commit()
    async with maintenance_engine.begin() as owner:
        assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await owner.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        await owner.execute(
            text(
                "INSERT INTO sync_physical_stock_baseline(tenant_id,branch_id,target_edge_node_id,"
                "snapshot_id,snapshot_hash,physical_records_hash,configuration_hash,origin_node_id,"
                "writer_epoch,sequence,source_checksum,projection_checksum) VALUES("
                ":tenant_id,:branch_id,:target_edge_node_id,:snapshot_id,:snapshot_hash,"
                ":physical_records_hash,:configuration_hash,:origin_node_id,:writer_epoch,"
                ":sequence,:source_checksum,:projection_checksum)"
            ),
            anchor.model_dump(),
        )
    return BaselineCase(snapshot, records, configs, anchor)


async def produce_and_stage(
    session: AsyncSession, maintenance_engine: AsyncEngine, case: BaselineCase
) -> tuple[Captured, ...]:
    await app_context(session)
    await session.execute(text("SELECT set_config('app.edge_node_id','',true)"))
    source = case.snapshot.source
    await dispense(session, source, (cart_line(source, "unit", 1),))
    await post_writeoff(session, source, selection(closed=1))
    await session.execute(
        text("UPDATE tenant_catalog SET base_price=77 WHERE id=:id"),
        {"id": source.incoming.stock.catalog_id},
    )
    await finish_sources(session)
    events = tuple(
        row for row in await captured(session) if row.event.sequence > case.anchor.sequence
    )
    assert len(events) == 3
    await session.commit()
    async with maintenance_engine.begin() as owner:
        assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
        for row in events:
            await owner.execute(
                text(
                    "INSERT INTO sync_physical_stock_anchor(event_id,tenant_id,branch_id,"
                    "target_edge_node_id,origin_node_id,writer_epoch,sequence,previous_source_checksum,"
                    "previous_projection_checksum,event_fingerprint) VALUES(:event_id,:tenant_id,"
                    ":branch_id,:target_edge_node_id,:origin_node_id,:writer_epoch,:sequence,"
                    ":previous_source_checksum,:previous_projection_checksum,:event_fingerprint)"
                ),
                {
                    **row.anchor.model_dump(),
                    "event_id": row.event.event_id,
                    "target_edge_node_id": case.anchor.target_edge_node_id,
                },
            )
    return events


async def read_view(
    session: AsyncSession, case: BaselineCase, *, actor_id: UUID | None = None
) -> PhysicalStockProjection:
    return await materialize_physical_stock_projection(
        SyncEdgeRepository(session),
        tenant_id=case.anchor.tenant_id,
        actor_id=actor_id or case.snapshot.runtime.actor_id,
        branch_id=case.anchor.branch_id,
        edge_node_id=case.anchor.target_edge_node_id,
        snapshot_id=case.anchor.snapshot_id,
        physical_records=case.records,
        configuration=case.configuration,
    )


async def readonly_facts(session: AsyncSession, case: BaselineCase) -> dict[str, object]:
    result: dict[str, object] = {"business": await protected_facts(session)}
    for table in ("sync_inbox", "sync_cursor", "sync_physical_stock_projection", "audit_log"):
        result[table] = (
            await session.execute(
                text(
                    "SELECT count(*),md5(coalesce(string_agg(to_jsonb(r)::text,'' "
                    f"ORDER BY to_jsonb(r)::text),'')) FROM {table} r WHERE tenant_id=:tenant"
                ),
                {"tenant": case.anchor.tenant_id},
            )
        ).one()
    return result


async def test_real_signed_baseline_applied_events_and_materializer_have_exact_authorized_state(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    case = await trusted_baseline(db_session, maintenance_engine)
    events = await produce_and_stage(db_session, maintenance_engine, case)
    await scoped_context(db_session, case)
    pull = SyncPullResponse(
        edge_node_id=case.anchor.target_edge_node_id,
        tenant_id=case.anchor.tenant_id,
        branch_id=case.anchor.branch_id,
        origin_node_id=case.anchor.origin_node_id,
        writer_epoch=case.anchor.writer_epoch,
        effective_after_sequence=case.anchor.sequence,
        after_source_checksum=case.anchor.source_checksum,
        after_projection_checksum=case.anchor.projection_checksum,
        cloud_last_sequence=events[-1].event.sequence,
        events=[row.event for row in events],
        has_more=False,
    )
    applied = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(pull)
    assert applied.status == "synced" and applied.applied == 3
    await repeatable_context(db_session)
    await scoped_context(db_session, case)
    source = await read_physical_source_facts(
        db_session, tenant_id=case.anchor.tenant_id, branch_id=case.anchor.branch_id
    )
    expected = build_physical_source_records(
        source, tenant_id=case.anchor.tenant_id, branch_id=case.anchor.branch_id
    )
    configs = await configuration(db_session)
    await db_session.commit()
    await db_session.execute(text("SET TRANSACTION READ ONLY"))
    await scoped_context(db_session, case)
    assert await db_session.scalar(text("SHOW transaction_read_only")) == "on"
    before = await readonly_facts(db_session, case)
    result = await read_view(db_session, case)
    assert result.ready and result.last_sequence == events[-1].event.sequence
    assert result.source_checksum == events[-1].event.stream_checksum
    assert result.projection_checksum == events[-1].event.projection_checksum
    assert physical_baseline_records_hash(
        result.physical_records
    ) == physical_baseline_records_hash(expected)
    assert result.configuration == configs
    assert await readonly_facts(db_session, case) == before
    await db_session.commit()
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, case.snapshot.runtime.stock, permissions=("batches.view",)
    )
    await scoped_context(db_session, case)
    before_denial = await readonly_facts(db_session, case)
    await set_receiving_context(db_session, case.snapshot.runtime.stock, actor_id)
    with pytest.raises(PermissionDeniedError):
        await read_view(db_session, case, actor_id=actor_id)
    await scoped_context(db_session, case)
    assert await readonly_facts(db_session, case) == before_denial
