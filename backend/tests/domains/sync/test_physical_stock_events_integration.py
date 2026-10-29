"""Source-trigger capture is transactional and tenant scoped on disposable PostgreSQL."""

from dataclasses import dataclass
from itertools import pairwise
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.sync.physical_stock_events import (
    PhysicalStockChange,
    PhysicalStockEventAnchor,
    validate_physical_stock_event,
)
from app.domains.sync.schemas import SyncEventEnvelope
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_legacy_opened_transition import partial_source, physical_source
from tests.domains.inventory.test_legacy_transition_posting import post as post_transition
from tests.domains.inventory.test_legacy_transition_source import accepted_batch, record_count
from tests.domains.inventory.test_physical_writeoff_posting import post as post_writeoff
from tests.domains.inventory.test_physical_writeoff_posting import selection
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    cart_line,
    dispense,
    receive_source,
)
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    remaining as remaining_units,
)
from tests.domains.inventory.test_unit_physical_blocking import set_blocked
from tests.domains.pos.test_physical_cash_checkout import complete as complete_cash
from tests.domains.pos.test_physical_cash_refund import refund
from tests.domains.pos.test_retail_checkout_event_producer_integration import (
    enable_gate as enable_retail_gate,
)
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
PRIVATE_TABLES = (
    "sync_physical_stock_producer_gate",
    "sync_physical_stock_source",
    "sync_physical_stock_event",
    "sync_physical_stock_anchor",
    "sync_physical_stock_baseline",
)


async def app_context(session: AsyncSession) -> None:
    context = runtime(session)
    await set_receiving_context(session, context.stock, context.actor_id)
    await session.execute(
        text("SELECT set_config('app.branch_id',:branch,true)"),
        {"branch": str(context.stock.branch_id)},
    )


async def enable_stock_gate(session: AsyncSession, maintenance_engine: AsyncEngine) -> None:
    context = runtime(session)
    async with maintenance_engine.begin() as owner:
        assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await owner.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        await owner.execute(
            text(
                "INSERT INTO sync_physical_stock_producer_gate(tenant_id,id,enabled,created_by) "
                "VALUES(:tenant,:branch,true,:actor)"
            ),
            {
                "tenant": context.stock.tenant_id,
                "branch": context.stock.branch_id,
                "actor": context.actor_id,
            },
        )


async def finish_sources(session: AsyncSession) -> None:
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


@dataclass(frozen=True)
class Captured:
    event: SyncEventEnvelope
    anchor: PhysicalStockEventAnchor
    change: PhysicalStockChange


async def captured(session: AsyncSession) -> tuple[Captured, ...]:
    context = runtime(session)
    rows = (
        await session.execute(
            text(
                "SELECT envelope,previous_source_checksum,previous_projection_checksum,"
                "event_fingerprint FROM sync_physical_stock_event "
                "WHERE tenant_id=:tenant AND branch_id=:branch ORDER BY sequence"
            ),
            {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id},
        )
    ).all()
    result: list[Captured] = []
    for row in rows:
        event = SyncEventEnvelope.model_validate(row.envelope)
        anchor = PhysicalStockEventAnchor(
            tenant_id=event.tenant_id,
            branch_id=event.branch_id,
            origin_node_id=event.origin_node_id,
            writer_epoch=event.writer_epoch,
            sequence=event.sequence,
            previous_source_checksum=row.previous_source_checksum,
            previous_projection_checksum=row.previous_projection_checksum,
            event_fingerprint=row.event_fingerprint,
        )
        change = validate_physical_stock_event(
            event,
            previous_source_checksum=anchor.previous_source_checksum,
            previous_projection_checksum=anchor.previous_projection_checksum,
        )
        result.append(Captured(event, anchor, change))
    return tuple(result)


async def test_disabled_stock_gate_never_captures_or_advances_the_shared_stream(
    db_session: AsyncSession,
) -> None:
    await app_context(db_session)
    before = await stream_facts(db_session)
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 1),))
    await post_writeoff(db_session, source, selection(closed=1))
    await set_blocked(db_session, source, True)
    await finish_sources(db_session)
    assert await captured(db_session) == ()
    assert await stream_facts(db_session) == before


async def test_config_initialization_standalone_open_and_writeoff_share_exact_branch_history(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    await enable_stock_gate(db_session, maintenance_engine)
    await app_context(db_session)
    before = await stream_facts(db_session)
    source = await receive_source(db_session)
    line = cart_line(source, "unit", 1)
    command_id, writeoff_id = uuid4(), uuid4()
    first = await dispense(db_session, source, (line,), command_id=command_id)
    selected = selection(closed=1)
    written_off = await post_writeoff(db_session, source, selected, operation_id=writeoff_id)
    await set_blocked(db_session, source, True)
    await db_session.execute(
        text("UPDATE tenant_catalog SET base_price=77 WHERE id=:id"),
        {"id": source.incoming.stock.catalog_id},
    )
    await db_session.execute(
        text("UPDATE tenant_catalog SET base_price=88 WHERE id=:id"),
        {"id": source.incoming.stock.catalog_id},
    )
    await finish_sources(db_session)
    events = await captured(db_session)
    assert {"catalog_config", "initialize", "dispense", "writeoff", "block"} <= {
        row.change.source_kind for row in events
    }
    baseline = before["streams"][0].last_sequence
    assert [row.event.sequence for row in events] == list(
        range(baseline + 1, baseline + 1 + len(events))
    )
    for previous, following in pairwise(events):
        assert following.anchor.previous_source_checksum == previous.event.stream_checksum
        assert following.anchor.previous_projection_checksum == previous.event.projection_checksum
    opened = next(row.change for row in events if row.change.source_kind == "dispense")
    initialized = next(row.change for row in events if row.change.source_kind == "initialize")
    assert initialized.initializations[0].closed_packs == 2
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE id=:id"), {"id": source.lot_id}
        )
        == 0
    )
    assert opened.source_id == command_id and opened.source_owner == "standalone"
    assert {m.kind for m in opened.movements} == {"pack_open", "unit_take"}
    assert {m.id for m in opened.movements} == {m.id for m in first.movements}
    assert all(m.pack_id is not None and m.pack_ordinal == 1 for m in opened.movements)
    assert (
        next(row.change for row in events if row.change.source_kind == "writeoff").source_id
        == writeoff_id
    )
    configs = [row.change.configuration for row in events if row.change.configuration is not None]
    assert [config.base_price for config in configs[-2:]] == [77, 88]
    assert configs[-1].revision == configs[-2].revision + 1
    assert await dispense(db_session, source, (line,), command_id=command_id) == first
    assert (
        await post_writeoff(db_session, source, selected, operation_id=writeoff_id) == written_off
    )
    await finish_sources(db_session)
    assert await captured(db_session) == events
    for table in ("sale", "sale_payment", "pos_physical_checkout", "pos_mixed_checkout"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": runtime(db_session).stock.tenant_id},
            )
            == 0
        )


async def test_outer_rollback_removes_source_binding_event_and_stream_reservation(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    await db_session.commit()
    await enable_stock_gate(db_session, maintenance_engine)
    await app_context(db_session)
    before = await stream_facts(db_session)
    command_id = uuid4()
    line = cart_line(source, "unit", 1)
    savepoint = await db_session.begin_nested()
    await dispense(db_session, source, (line,), command_id=command_id)
    await finish_sources(db_session)
    assert len(await captured(db_session)) == 1
    await savepoint.rollback()
    assert await captured(db_session) == ()
    assert await stream_facts(db_session) == before
    await dispense(db_session, source, (line,), command_id=command_id)
    await finish_sources(db_session)
    assert len(await captured(db_session)) == 1


@pytest.mark.parametrize("path", PATHS)
async def test_real_checkout_has_one_stock_companion_and_replay_never_recaptures(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    path: PathKind,
) -> None:
    await enable_stock_gate(db_session, maintenance_engine)
    await enable_retail_gate(db_session, maintenance_engine)
    await app_context(db_session)
    completion = await prepare(db_session, path)
    result = await finish(db_session, completion)
    events = await captured(db_session)
    companions = [row for row in events if row.change.source_owner == "checkout"]
    assert len(companions) == 1
    stock = companions[0]
    assert stock.change.source_id == result.stock_command_id
    assert stock.change.checkout_sale_id == result.sale_id
    assert stock.change.checkout_event_id == result.event_id
    assert stock.event.operation_id != result.operation_id
    outbox = (
        await db_session.execute(
            text(
                "SELECT event_id,sequence,event_type FROM sync_outbox "
                "WHERE tenant_id=:tenant ORDER BY sequence"
            ),
            {"tenant": result.tenant_id},
        )
    ).all()
    assert len(outbox) == len(events) + 1
    assert [row.sequence for row in outbox] == list(
        range(outbox[0].sequence, outbox[0].sequence + len(outbox))
    )
    assert len([row for row in outbox if row.event_id == result.event_id]) == 1
    before = await protected_facts(db_session)
    assert await finish(db_session, completion) == result
    assert await captured(db_session) == events
    assert await protected_facts(db_session) == before


async def test_physical_refund_keeps_quarantine_out_of_available_stock_stream(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    await enable_stock_gate(db_session, maintenance_engine)
    await enable_retail_gate(db_session, maintenance_engine)
    await app_context(db_session)
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack", quantity=1)
    await complete_cash(db_session, await saved_request(db_session, source.sale))
    await finish_sources(db_session)
    before = await captured(db_session)
    operation_id = uuid4()
    result = await refund(
        db_session,
        source.sale.id,
        item.id,
        source.sale.shift_id,
        operation_id=operation_id,
        quantity=1,
    )
    await finish_sources(db_session)
    assert await captured(db_session) == before
    assert (
        await refund(
            db_session,
            source.sale.id,
            item.id,
            source.sale.shift_id,
            operation_id=operation_id,
            quantity=1,
        )
        == result
    )
    await finish_sources(db_session)
    assert await captured(db_session) == before


@pytest.mark.parametrize("table", PRIVATE_TABLES)
@pytest.mark.parametrize("action", ["INSERT", "UPDATE", "DELETE"])
async def test_app_cannot_forge_gate_source_trust_or_rewrite_capture(
    db_session: AsyncSession,
    table: str,
    action: str,
) -> None:
    await app_context(db_session)
    statements = {
        "INSERT": f"INSERT INTO {table} DEFAULT VALUES",
        "UPDATE": f"UPDATE {table} SET created_at=clock_timestamp()",
        "DELETE": f"DELETE FROM {table}",
    }
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(text(statements[action]))
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize("scope", ["tenant", "branch", "missing-branch"])
async def test_physical_event_history_is_invisible_outside_real_tenant_and_branch(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    scope: str,
) -> None:
    await enable_stock_gate(db_session, maintenance_engine)
    await app_context(db_session)
    await receive_source(db_session)
    await finish_sources(db_session)
    assert await captured(db_session)
    field = "app.tenant_id" if scope == "tenant" else "app.branch_id"
    value = "" if scope == "missing-branch" else str(uuid4())
    await db_session.execute(
        text("SELECT set_config(:field,:value,true)"), {"field": field, "value": value}
    )
    assert await captured(db_session) == ()


async def test_new_tenant_tables_force_rls_and_keep_sensitive_source_private(
    db_session: AsyncSession,
) -> None:
    for table in (*PRIVATE_TABLES, "sync_physical_stock_projection"):
        flags = (
            await db_session.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:table"
                ),
                {"table": table},
            )
        ).one()
        assert tuple(flags) == (True, True)
    for table in ("sync_physical_stock_producer_gate", "sync_physical_stock_source"):
        with pytest.raises(DBAPIError) as denied:
            async with db_session.begin_nested():
                await db_session.execute(text(f"SELECT * FROM {table}"))
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"


async def test_source_cannot_be_mutated_again_after_forced_current_transaction_dispatch(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    await db_session.commit()
    await enable_stock_gate(db_session, maintenance_engine)
    await app_context(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 1),))
    await finish_sources(db_session)
    before = await captured(db_session)
    stream_before = await stream_facts(db_session)
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE tenant_catalog SET base_price=77 WHERE id=:id"),
                {"id": source.incoming.stock.catalog_id},
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    assert "physical_stock_capture_after_dispatch" in str(denied.value.orig)
    assert await captured(db_session) == before
    assert await stream_facts(db_session) == stream_before


async def test_privileged_support_catalog_write_cannot_leave_an_enabled_stream_gap(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    await enable_stock_gate(db_session, maintenance_engine)
    await app_context(db_session)
    context = runtime(db_session)
    before = await db_session.scalar(
        text("SELECT to_jsonb(row) FROM tenant_catalog row WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    async with AsyncSession(db_engine) as support:
        assert str(await support.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await support.scalar(text("SELECT session_user")) == "aurum_support"
        await support.execute(
            text(
                "SELECT set_config('app.support_session','true',true),"
                "set_config('app.tenant_id',:tenant,true),set_config('app.user_id',:actor,true)"
            ),
            {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
        )
        with pytest.raises(DBAPIError) as denied:
            async with support.begin_nested():
                await support.execute(
                    text("UPDATE tenant_catalog SET base_price=77 WHERE id=:id"),
                    {"id": context.stock.catalog_id},
                )
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
        assert "physical_stock_runtime_writer_unsupported" in str(denied.value.orig)
    assert (
        await db_session.scalar(
            text("SELECT to_jsonb(row) FROM tenant_catalog row WHERE id=:id"),
            {"id": context.stock.catalog_id},
        )
        == before
    )
    assert await captured(db_session) == ()


@pytest.mark.parametrize(
    ("catalog_first", "financial_savepoint"), [(True, False), (False, False), (False, True)]
)
async def test_catalog_fanout_and_reserved_financial_stream_fail_closed_before_mixed_branch_locks(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    catalog_first: bool,
    financial_savepoint: bool,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    context = runtime(db_session)
    await db_session.commit()
    await enable_stock_gate(db_session, maintenance_engine)
    await enable_retail_gate(db_session, maintenance_engine)
    async with AsyncSession(maintenance_engine, expire_on_commit=False) as owner:
        async with owner.begin():
            assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
            await owner.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"),
                {"tenant": str(context.stock.tenant_id)},
            )
            other = await FoundationService(FoundationRepository(owner)).create_branch(
                tenant_id=context.stock.tenant_id, fields={"name": "Independent stock stream"}
            )
            await owner.execute(
                text(
                    "INSERT INTO sync_physical_stock_producer_gate(tenant_id,id,enabled) "
                    "VALUES(:tenant,:branch,true)"
                ),
                {"tenant": context.stock.tenant_id, "branch": other.id},
            )
    await app_context(db_session)
    before = await protected_facts(db_session)

    async def change_catalog() -> None:
        await db_session.execute(
            text("UPDATE tenant_catalog SET base_price=77 WHERE id=:id"),
            {"id": source.stock.incoming.stock.catalog_id},
        )

    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            if catalog_first:
                await change_catalog()
            if financial_savepoint:
                async with db_session.begin_nested():
                    await complete_cash(db_session, request)
            else:
                await complete_cash(db_session, request)
            if not catalog_first:
                await change_catalog()
            await finish_sources(db_session)
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    assert "physical_stock_mixed_branch_transaction" in str(denied.value.orig)
    assert await protected_facts(db_session) == before
    assert await captured(db_session) == ()


@pytest.mark.parametrize("opened_basis", [False, True], ids=["closed", "opened"])
async def test_gated_legacy_transition_preserves_original_basis_after_same_transaction_consumption(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    opened_basis: bool,
) -> None:
    if opened_basis:
        legacy = await partial_source(db_session)
        batch_id, record_id = legacy.batch_id, legacy.record_id
    else:
        batch_id = await accepted_batch(db_session)
        record_id = await record_count(db_session, batch_id)
    await db_session.commit()
    await enable_stock_gate(db_session, maintenance_engine)
    await app_context(db_session)
    command_id = uuid4()
    transition = await post_transition(db_session, batch_id, record_id, command_id)
    source = await physical_source(db_session, transition["lot_id"])
    await dispense(db_session, source, (cart_line(source, "unit", 1),))
    await post_writeoff(db_session, source, selection(closed=1))
    await finish_sources(db_session)
    events = await captured(db_session)
    assert [row.change.source_kind for row in events] == [
        "legacy_transition",
        "dispense",
        "writeoff",
    ]
    initialized = events[0].change
    assert initialized.source_id == transition["operation_id"]
    assert len(initialized.initializations) == 1
    initial = initialized.initializations[0]
    assert initial.id == source.lot_id
    assert initial.closed_packs == initial.initial_closed_packs == (1 if opened_basis else 2)
    assert initial.initial_opened_packs == int(opened_basis)
    assert initial.initial_opened_units == (15 if opened_basis else 0)
    assert [pack.remaining_units for pack in initial.opened_packs] == ([15] if opened_basis else [])
    assert len(initial.opening_basis) == int(opened_basis)
    assert (
        initial.cost.status == "known" and initial.cost.remaining_cost == initial.cost.initial_cost
    )
    assert await remaining_units(db_session, source) == (14 if opened_basis else 29)
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE id=:id"), {"id": source.lot_id}
        )
        == 0
    )
    assert await post_transition(db_session, batch_id, record_id, command_id) == transition
    await finish_sources(db_session)
    assert await captured(db_session) == events
