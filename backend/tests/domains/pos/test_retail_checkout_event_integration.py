"""Candidate preparation reads actual saved checkouts without reserving a stream."""

import json
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.models import Sale
from app.domains.pos.retail_checkout_event_service import (
    RetailCheckoutEventCandidate,
    RetailCheckoutEventService,
)
from app.domains.pos.retail_checkout_origin_repository import (
    RetailCheckoutOriginRepository,
    RetailCheckoutOriginSnapshot,
)
from app.domains.pos.retail_sale_event_storage import decode_retail_sale_event
from app.domains.pos.retail_sale_events import RetailCheckoutResult, RetailSaleStreamPosition
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_retail_checkout_origin_integration import (
    finish,
    prepare,
    read_origin,
)
from tests.domains.pos.test_retail_checkout_source_integration import (
    PATHS,
    PathKind,
    protected_facts,
)
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def position(origin: RetailCheckoutOriginSnapshot) -> RetailSaleStreamPosition:
    # These values are supplied candidate metadata. Reading a checkout does not
    # reserve this position or verify either prior checksum against a stream.
    return RetailSaleStreamPosition(
        origin_node_id=origin.origin_node_id,
        writer_epoch=origin.writer_epoch,
        sequence=13,
        previous_source_checksum="a" * 64,
        previous_projection_checksum="b" * 64,
    )


async def candidate(
    session: AsyncSession,
    result: RetailCheckoutResult,
    supplied: RetailSaleStreamPosition,
    **changes: object,
) -> RetailCheckoutEventCandidate:
    arguments = {
        "tenant_id": result.tenant_id,
        "sale_id": result.sale_id,
        "operation_id": result.operation_id,
        "actor_id": result.cashier_id,
        "allowed_branch_ids": {result.branch_id},
        "position": supplied,
        **changes,
    }
    return await RetailCheckoutEventService(RetailCheckoutOriginRepository(session)).prepare(
        **arguments
    )


async def facts(session: AsyncSession) -> dict[str, object]:
    origins = list(
        (
            await session.execute(
                text("""
                    SELECT to_jsonb(row) FROM pos_retail_checkout_origin row
                    WHERE tenant_id=:tenant ORDER BY id
                """),
                {"tenant": runtime(session).stock.tenant_id},
            )
        ).scalars()
    )
    return {"protected": await protected_facts(session), "origins": origins}


@pytest.mark.parametrize("path", PATHS)
async def test_each_real_saved_checkout_prepares_repeatable_source_bound_bytes_without_writes(
    db_session: AsyncSession, path: PathKind
) -> None:
    source = await finish(db_session, await prepare(db_session, path))
    captured = await read_origin(db_session, source)
    assert captured is not None
    before = await facts(db_session)
    first = await candidate(db_session, source, position(captured))
    second = await candidate(db_session, source, position(captured))
    assert first == second
    assert type(first.record) is bytes
    assert first.anchor.origin_node_id == captured.origin_node_id
    assert first.anchor.writer_epoch == captured.writer_epoch
    assert first.anchor.sequence == 13
    assert first.anchor.previous_source_checksum == "a" * 64
    assert first.anchor.previous_projection_checksum == "b" * 64
    restored = decode_retail_sale_event(first.record, anchor=first.anchor)
    assert restored == source
    assert restored.event_id == restored.operation_id == restored.stock_command_id
    assert restored.operation_id == source.operation_id
    stored = json.loads(first.record)["envelope"]
    assert stored["event_type"] == f"pos.sale.completed.v{source.schema_version}"
    assert stored["payload"] == source.model_dump(mode="json")
    assert await read_origin(db_session, source) == captured
    assert await facts(db_session) == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted


async def test_candidate_uses_historical_facts_after_catalog_shift_and_tenant_changes(
    db_session: AsyncSession,
) -> None:
    source = await finish(db_session, await prepare(db_session, "mixed-electronic"))
    captured = await read_origin(db_session, source)
    assert captured is not None
    original = await candidate(db_session, source, position(captured))
    await close_shift(db_session, source.shift_id)
    await db_session.execute(
        text("""
            UPDATE tenant_catalog SET base_price=99.99,brand_name='Changed after checkout'
            WHERE tenant_id=:tenant
        """),
        {"tenant": source.tenant_id},
    )
    await db_session.execute(
        text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
        {"tenant": source.tenant_id},
    )
    before = await facts(db_session)
    replay = await candidate(db_session, source, position(captured))
    assert replay == original
    assert decode_retail_sale_event(replay.record, anchor=replay.anchor) == source
    assert await facts(db_session) == before


@pytest.mark.parametrize("field", ["origin_node_id", "writer_epoch"])
async def test_wrong_writer_position_is_rejected_without_changing_persisted_state(
    db_session: AsyncSession, field: str
) -> None:
    source = await finish(db_session, await prepare(db_session, "physical-cash"))
    captured = await read_origin(db_session, source)
    assert captured is not None
    before = await facts(db_session)
    wrong: object = UUID(int=99) if field == "origin_node_id" else captured.writer_epoch + 1
    with pytest.raises(ConflictError, match="captured checkout writer"):
        await candidate(db_session, source, position(captured).model_copy(update={field: wrong}))
    assert await facts(db_session) == before


async def test_actual_current_actor_branch_and_foreign_tenant_denials_precede_position_parsing(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    # The seed helpers use separate tenant-access locks: prepare all independent
    # actors before the main checkout transaction acquires those same locks.
    other_actor = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            assert str(await support.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await support.scalar(text("SELECT current_user")) == "aurum_support"
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            foreign = await seed_ledger_context(support)
    foreign_actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    source = await finish(db_session, await prepare(db_session, "mixed-cash"))
    captured = await read_origin(db_session, source)
    assert captured is not None
    invalid = position(captured).model_copy(update={"sequence": False})
    before = await facts(db_session)
    try:
        with pytest.raises(PermissionDeniedError):
            await candidate(db_session, source, invalid, allowed_branch_ids=set())
        await set_receiving_context(db_session, context.stock, other_actor)
        with pytest.raises(PermissionDeniedError, match="cashier"):
            await candidate(db_session, source, invalid, actor_id=other_actor)
        await set_receiving_context(db_session, foreign, foreign_actor)
        assert await db_session.scalar(text("SELECT session_user")) == "aurum_app"
        with pytest.raises(NotFoundError):
            await candidate(
                db_session,
                source,
                invalid,
                tenant_id=foreign.tenant_id,
                actor_id=foreign_actor,
                allowed_branch_ids={foreign.branch_id},
            )
    finally:
        await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before
    restored = await candidate(db_session, source, position(captured))
    assert decode_retail_sale_event(restored.record, anchor=restored.anchor) == source
    assert await facts(db_session) == before


async def test_pending_identity_map_changes_remain_pending_without_flush_refresh_or_rollback(
    db_session: AsyncSession,
) -> None:
    source = await finish(db_session, await prepare(db_session, "physical-cash"))
    captured = await read_origin(db_session, source)
    assert captured is not None
    sale = await db_session.get(Sale, source.sale_id)
    assert sale is not None
    before = await facts(db_session)
    sale.currency = "USD"
    pending = set(db_session.dirty)
    with pytest.raises(ConflictError, match="clean session"):
        await candidate(db_session, source, position(captured))
    assert set(db_session.dirty) == pending
    assert sale.currency == "USD"
    with db_session.no_autoflush:
        assert (
            await db_session.scalar(
                text("SELECT currency FROM sale WHERE id=:sale"), {"sale": source.sale_id}
            )
            == "TJS"
        )
        assert await facts(db_session) == before
    assert set(db_session.dirty) == pending
