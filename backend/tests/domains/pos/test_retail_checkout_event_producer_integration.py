"""Gated event production is atomic with real checkout transactions in CI only."""

import asyncio
import json
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.pos.repository import POSRepository
from app.domains.pos.retail_checkout_event_producer import RetailCheckoutEventProducer
from app.domains.pos.retail_checkout_event_repository import RetailCheckoutEventRepository
from app.domains.pos.retail_checkout_event_service import RetailCheckoutEventCandidate
from app.domains.pos.retail_sale_event_storage import (
    build_retail_sale_event_anchor,
    decode_retail_sale_event,
    encode_retail_sale_event,
)
from app.domains.pos.retail_sale_events import (
    RetailCheckoutResult,
    RetailSaleStreamPosition,
    build_retail_sale_event,
)
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_cash_checkout import complete as complete_mixed
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cash_checkout import complete as complete_physical
from tests.domains.pos.test_retail_checkout_event_integration import facts
from tests.domains.pos.test_retail_checkout_origin_integration import finish, prepare, stream_facts
from tests.domains.pos.test_retail_checkout_source_integration import PATHS, PathKind
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def enable_gate(session: AsyncSession, maintenance_engine: AsyncEngine) -> None:
    context = runtime(session)
    async with maintenance_engine.begin() as owner:
        assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await owner.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        await owner.execute(
            text("""
                INSERT INTO sync_retail_event_producer_gate(tenant_id,id,enabled,created_by)
                VALUES(:tenant,:branch,true,:actor)
                ON CONFLICT(tenant_id,id) DO UPDATE SET enabled=true
            """),
            {
                "tenant": context.stock.tenant_id,
                "branch": context.stock.branch_id,
                "actor": context.actor_id,
            },
        )


async def produce(
    session: AsyncSession, source: RetailCheckoutResult, **changes: object
) -> RetailCheckoutEventCandidate | None:
    arguments = {
        "tenant_id": source.tenant_id,
        "sale_id": source.sale_id,
        "operation_id": source.operation_id,
        "actor_id": source.cashier_id,
        "allowed_branch_ids": {source.branch_id},
        **changes,
    }
    return await RetailCheckoutEventProducer(RetailCheckoutEventRepository(session)).produce(
        **arguments
    )


async def event_facts(session: AsyncSession) -> dict[str, object]:
    return {
        "checkout": await facts(session),
        "events": list(
            (
                await session.execute(
                    text(
                        "SELECT to_jsonb(row)-'record' FROM pos_retail_checkout_event row "
                        "WHERE tenant_id=:tenant ORDER BY id"
                    ),
                    {"tenant": runtime(session).stock.tenant_id},
                )
            ).scalars()
        ),
    }


@pytest.mark.parametrize("path", PATHS)
async def test_default_off_four_checkout_paths_never_reserve_or_write_retail_events(
    db_session: AsyncSession, path: PathKind
) -> None:
    completion = await prepare(db_session, path)
    before_stream = await stream_facts(db_session)
    source = await finish(db_session, completion)
    assert await produce(db_session, source) is None
    assert await stream_facts(db_session) == before_stream
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_retail_checkout_event WHERE tenant_id=:tenant"),
            {"tenant": source.tenant_id},
        )
        == 0
    )
    assert (
        await db_session.scalar(
            text("SELECT event_capture_required FROM pos_retail_checkout_origin WHERE id=:id"),
            {"id": source.operation_id},
        )
        is False
    )


@pytest.mark.parametrize("path", PATHS)
async def test_enabled_four_checkout_paths_commit_one_bound_event_and_exact_replay(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, path: PathKind
) -> None:
    await enable_gate(db_session, maintenance_engine)
    completion = await prepare(db_session, path)
    before_stream = await stream_facts(db_session)
    source = await finish(db_session, completion)
    saved = await RetailCheckoutEventRepository(db_session).read(
        source.tenant_id, source.sale_id, source.operation_id
    )
    assert saved is not None and saved.event_id == source.event_id
    candidate = await produce(db_session, source)
    assert candidate is not None
    assert decode_retail_sale_event(candidate.record, anchor=candidate.anchor) == source
    assert candidate.record == saved.record
    assert candidate.anchor.sequence == saved.sequence
    assert saved.previous_source_checksum == before_stream["streams"][0].current_checksum
    assert (
        saved.previous_projection_checksum
        == before_stream["streams"][0].current_projection_checksum
    )
    envelope = json.loads(candidate.record)["envelope"]
    outbox = (
        await db_session.execute(
            text(
                "SELECT event_type,schema_version,sequence,payload,payload_hash,stream_checksum,"
                "projection_hash,projection_checksum FROM sync_outbox WHERE event_id=:id"
            ),
            {"id": source.event_id},
        )
    ).one()
    assert outbox.event_type == f"pos.sale.completed.v{source.schema_version}"
    assert outbox.schema_version == source.schema_version
    assert outbox.sequence == saved.sequence
    assert outbox.payload == source.model_dump(mode="json")
    for field in ("payload_hash", "stream_checksum", "projection_hash", "projection_checksum"):
        assert getattr(outbox, field) == envelope[field]
    assert saved.sequence == before_stream["streams"][0].last_sequence + 1
    before_replay = await event_facts(db_session)
    assert await finish(db_session, completion) == source
    assert await produce(db_session, source) == candidate
    assert await event_facts(db_session) == before_replay
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await produce(db_session, source) == candidate
    assert await event_facts(db_session) == before_replay


@pytest.mark.parametrize("failure", ["store", "validate"])
async def test_event_store_or_final_validation_failure_rolls_back_entire_checkout(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    await enable_gate(db_session, maintenance_engine)
    completion = await prepare(db_session, "mixed-electronic")
    before = await event_facts(db_session)
    method = "store" if failure == "store" else "validate_completion"

    async def refuse(_self: object, *arguments: object, **values: object) -> None:
        raise RuntimeError("Injected event persistence failure")

    monkeypatch.setattr(RetailCheckoutEventRepository, method, refuse)
    with pytest.raises(RuntimeError, match="Injected event persistence failure"):
        await completion()
    assert await event_facts(db_session) == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted


async def test_dormant_captured_decision_cannot_be_backfilled_by_a_later_gate_enable(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await finish(db_session, await prepare(db_session, "physical-cash"))
    context = runtime(db_session)
    await db_session.commit()
    await enable_gate(db_session, maintenance_engine)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await event_facts(db_session)
    assert await produce(db_session, source) is None
    assert await event_facts(db_session) == before
    assert (
        await db_session.scalar(
            text("SELECT event_capture_required FROM pos_retail_checkout_origin WHERE id=:id"),
            {"id": source.operation_id},
        )
        is False
    )


async def suppress_producer(_self: object, **arguments: object) -> None:
    """Exercise the database's required-event guard, not a fake financial source."""


async def test_database_deferred_guard_refuses_required_checkout_without_the_producer(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await enable_gate(db_session, maintenance_engine)
    completion = await prepare(db_session, "physical-cash")
    before = await event_facts(db_session)
    monkeypatch.setattr(RetailCheckoutEventProducer, "produce", suppress_producer)
    point = await db_session.begin_nested()
    try:
        await completion()
        with pytest.raises(DBAPIError, match="retail_event_required") as denied:
            await db_session.execute(
                text("SET CONSTRAINTS trg_retail_checkout_event_required IMMEDIATE")
            )
        assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    finally:
        await point.rollback()
    assert await event_facts(db_session) == before


async def unstored_candidate(
    session: AsyncSession, maintenance_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> tuple[RetailCheckoutResult, RetailCheckoutEventCandidate, RetailSaleStreamPosition]:
    await enable_gate(session, maintenance_engine)
    completion = await prepare(session, "mixed-cash")
    monkeypatch.setattr(RetailCheckoutEventProducer, "produce", suppress_producer)
    source = await completion()
    repo = RetailCheckoutEventRepository(session)
    reserved = await repo.reserve(source.tenant_id, source.sale_id, source.operation_id)
    position = RetailSaleStreamPosition(
        origin_node_id=reserved.origin_node_id,
        writer_epoch=reserved.writer_epoch,
        sequence=reserved.sequence,
        previous_source_checksum=reserved.previous_checksum,
        previous_projection_checksum=reserved.previous_projection_checksum,
    )
    envelope = build_retail_sale_event(source, position=position)
    anchor = build_retail_sale_event_anchor(
        envelope,
        previous_source_checksum=position.previous_source_checksum,
        previous_projection_checksum=position.previous_projection_checksum,
    )
    return (
        source,
        RetailCheckoutEventCandidate(anchor, encode_retail_sale_event(envelope, anchor=anchor)),
        position,
    )


@pytest.mark.parametrize(
    "change", ["duplicate-json", "fully-rehashed-source", "fingerprint", "sequence"]
)
async def test_direct_store_cannot_accept_rehashed_or_malformed_source_evidence(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    source, candidate, position = await unstored_candidate(
        db_session, maintenance_engine, monkeypatch
    )
    record, fingerprint = candidate.record, candidate.anchor.event_fingerprint
    if change == "duplicate-json":
        record = record[:-1] + b',"storage_version":1}'
    elif change == "fingerprint":
        fingerprint = "f" * 64
    else:
        substituted = source
        if change == "fully-rehashed-source":
            lines = list(source.lines)
            lines[-1] = lines[-1].model_copy(update={"name": "Substituted saved medicine"})
            substituted = source.model_copy(update={"lines": lines})
        else:
            position = position.model_copy(update={"sequence": position.sequence + 1})
        envelope = build_retail_sale_event(substituted, position=position)
        anchor = build_retail_sale_event_anchor(
            envelope,
            previous_source_checksum=position.previous_source_checksum,
            previous_projection_checksum=position.previous_projection_checksum,
        )
        record = encode_retail_sale_event(envelope, anchor=anchor)
        fingerprint = anchor.event_fingerprint
    before = await event_facts(db_session)
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await RetailCheckoutEventRepository(db_session).store(
                source.tenant_id,
                source.sale_id,
                source.operation_id,
                record=record,
                previous_source_checksum=position.previous_source_checksum,
                previous_projection_checksum=position.previous_projection_checksum,
                event_fingerprint=fingerprint,
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    assert await event_facts(db_session) == before


async def test_concurrent_exact_checkout_retry_waits_and_replays_one_reserved_event(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    await enable_gate(db_session, maintenance_engine)
    completion = await prepare(db_session, "physical-cash")
    # partial preserves the actual cash request while rebinding the second
    # connection; no hand-made completed journals or trigger bypasses are used.
    context = cast(ReceivingRuntime, runtime(db_session))
    request = completion.args[1]
    complete = completion.func
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    first = await completion()
    original = await produce(db_session, first)
    assert original is not None
    entered = asyncio.Event()

    async def retry() -> RetailCheckoutResult:
        async with context.factory() as second:
            second.info["receiving_runtime"] = context
            await set_receiving_context(second, context.stock, context.actor_id)
            entered.set()
            replay = await complete(second, request)
            await second.commit()
            return replay

    task = asyncio.create_task(retry())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        await db_session.commit()
        assert await asyncio.wait_for(task, timeout=10) == first
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert await produce(db_session, first) == original
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM pos_retail_checkout_event WHERE id=:id"),
                {"id": first.operation_id},
            )
            == 1
        )
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM sync_outbox WHERE event_id=:id"),
                {"id": first.event_id},
            )
            == 1
        )
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("competitor", ["mixed", "legacy"])
async def test_physical_and_mixed_or_legacy_sales_share_one_ordered_branch_stream(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, competitor: str
) -> None:
    await enable_gate(db_session, maintenance_engine)
    context = runtime(db_session)
    physical = await checkout_source(db_session)
    await put_physical(db_session, physical.sale.id, quantity=5)
    first_request = await saved_request(db_session, physical.sale)
    pos = POSService(POSRepository(db_session))
    second = await pos.create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=physical.sale.register_id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    await legacy_with_stock(db_session, second)
    second_request = None
    if competitor == "mixed":
        # Both physical portions consume the same real lot, branch and register.
        await put_physical(db_session, second.id, position=2, quantity=5)
        second_request = await saved_request(db_session, second)
    second_id = second.id
    legacy_operation_id = uuid4()
    register_id = physical.sale.register_id
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await stream_facts(db_session)
    first = await complete_physical(db_session, first_request)
    entered = asyncio.Event()

    async def complete_other() -> None:
        async with context.factory() as caller:
            caller.info["receiving_runtime"] = context
            await set_receiving_context(caller, context.stock, context.actor_id)
            entered.set()
            if competitor == "mixed":
                assert second_request is not None
                await complete_mixed(caller, second_request)
            else:
                # The v1 emitter is checkout(), used by the public atomic
                # endpoint. complete() finalizes a legacy sale without outbox.
                await POSService(POSRepository(caller)).checkout(
                    tenant_id=context.stock.tenant_id,
                    register_id=register_id,
                    cashier_user_id=context.actor_id,
                    operation_id=legacy_operation_id,
                    draft_sale_id=second_id,
                    items=[(context.stock.catalog_id, Decimal("0.001"))],
                    payments=[("cash", Decimal("0.02"), None)],
                    allowed_branch_ids={context.stock.branch_id},
                )
            await caller.commit()

    task = asyncio.create_task(complete_other())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        await db_session.commit()
        await asyncio.wait_for(task, timeout=10)
        await set_receiving_context(db_session, context.stock, context.actor_id)
        rows = (
            await db_session.execute(
                text(
                    "SELECT sequence,event_type,aggregate_id FROM sync_outbox "
                    "WHERE tenant_id=:tenant AND branch_id=:branch ORDER BY sequence"
                ),
                {"tenant": first.tenant_id, "branch": first.branch_id},
            )
        ).all()
        baseline = before["streams"][0].last_sequence
        assert [row.sequence for row in rows] == [baseline + 1, baseline + 2]
        assert [row.aggregate_id for row in rows] == [first.sale_id, second_id]
        assert [row.event_type for row in rows] == [
            "pos.sale.completed.v3",
            "pos.sale.completed.v4" if competitor == "mixed" else "pos.sale.completed.v1",
        ]
        assert await db_session.scalar(
            text("SELECT count(*) FROM pos_retail_checkout_event WHERE tenant_id=:tenant"),
            {"tenant": first.tenant_id},
        ) == (2 if competitor == "mixed" else 1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_event_rls_and_authority_remain_cashier_branch_and_tenant_scoped(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    other_actor = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            foreign = await seed_ledger_context(support)
    foreign_actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    await enable_gate(db_session, maintenance_engine)
    source = await finish(db_session, await prepare(db_session, "mixed-cash"))
    before = await event_facts(db_session)
    try:
        with pytest.raises(PermissionDeniedError):
            await produce(db_session, source, allowed_branch_ids=set())
        await set_receiving_context(db_session, context.stock, other_actor)
        assert (
            await RetailCheckoutEventRepository(db_session).read(
                source.tenant_id, source.sale_id, source.operation_id
            )
            is None
        )
        with pytest.raises(PermissionDeniedError):
            await produce(db_session, source, actor_id=other_actor)
        await set_receiving_context(db_session, foreign, foreign_actor)
        assert (
            await RetailCheckoutEventRepository(db_session).read(
                source.tenant_id, source.sale_id, source.operation_id
            )
            is None
        )
        with pytest.raises(NotFoundError):
            await produce(
                db_session,
                source,
                tenant_id=foreign.tenant_id,
                actor_id=foreign_actor,
                allowed_branch_ids={foreign.branch_id},
            )
    finally:
        await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await event_facts(db_session) == before
