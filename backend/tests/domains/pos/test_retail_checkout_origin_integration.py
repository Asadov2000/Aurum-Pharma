"""Database-captured origin identity uses actual checkout transactions in isolated CI."""

from collections.abc import Awaitable, Callable
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.pos.retail_checkout_origin_repository import (
    RetailCheckoutOriginRepository,
    RetailCheckoutOriginSnapshot,
)
from app.domains.pos.retail_checkout_origin_service import RetailCheckoutOriginService
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos import test_mixed_electronic_checkout as mixed_electronic
from tests.domains.pos import test_unit_electronic_checkout as physical_electronic
from tests.domains.pos.test_mixed_cash_checkout import complete as complete_mixed_cash
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cash_checkout import complete as complete_physical_cash
from tests.domains.pos.test_retail_checkout_source_integration import (
    PATHS,
    PathKind,
    protected_facts,
)
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
Completion = Callable[[], Awaitable[RetailCheckoutResult]]


async def prepare(session: AsyncSession, path: PathKind) -> Completion:
    if path == "physical-electronic":
        request, identifiers = await physical_electronic.prepared(session, ("cash", "card", "qr"))
        attempts = await physical_electronic.attempts(session, request, identifiers)
        await physical_electronic.confirm(session, attempts)
        return partial(physical_electronic.complete, session, request)
    if path == "mixed-electronic":
        request, identifiers = await mixed_electronic.prepared(session, ("cash", "card", "qr"))
        attempts = await mixed_electronic.attempts(session, request, identifiers)
        await mixed_electronic.confirm(session, request, attempts)
        return partial(mixed_electronic.complete, session, request)
    source = await checkout_source(session)
    if path == "mixed-cash":
        await legacy_with_stock(session, source.sale)
    await put_physical(session, source.sale.id)
    request = await saved_request(session, source.sale)
    return partial(
        complete_mixed_cash if path == "mixed-cash" else complete_physical_cash, session, request
    )


async def finish(session: AsyncSession, completion: Completion) -> RetailCheckoutResult:
    result = await completion()
    await session.flush()
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return result


async def stream_facts(session: AsyncSession) -> dict[str, object]:
    tenant = runtime(session).stock.tenant_id
    streams = (
        await session.execute(
            text("""
                SELECT branch_id,writer_node_id,writer_epoch,last_sequence,
                  current_checksum,current_projection_checksum
                FROM sync_stream WHERE tenant_id=:tenant ORDER BY branch_id
            """),
            {"tenant": tenant},
        )
    ).all()
    outbox = (
        await session.execute(
            text("""
                SELECT event_id,origin_node_id,writer_epoch,sequence,payload_hash,
                  stream_checksum,projection_hash,projection_checksum
                FROM sync_outbox WHERE tenant_id=:tenant ORDER BY event_id
            """),
            {"tenant": tenant},
        )
    ).all()
    return {"streams": streams, "outbox": outbox}


async def read_origin(
    session: AsyncSession, result: RetailCheckoutResult
) -> RetailCheckoutOriginSnapshot | None:
    return await RetailCheckoutOriginService(RetailCheckoutOriginRepository(session)).read(
        tenant_id=result.tenant_id,
        sale_id=result.sale_id,
        operation_id=result.operation_id,
        actor_id=result.cashier_id,
        allowed_branch_ids={result.branch_id},
    )


@pytest.mark.parametrize("path", PATHS)
async def test_each_real_checkout_captures_writer_once_without_emitting_or_advancing_stream(
    db_session: AsyncSession, path: PathKind
) -> None:
    completion = await prepare(db_session, path)
    before_stream = await stream_facts(db_session)
    writer = (
        await db_session.execute(
            text("""
                SELECT writer_node_id,writer_epoch FROM sync_stream
                WHERE tenant_id=:tenant AND branch_id=:branch
            """),
            {
                "tenant": runtime(db_session).stock.tenant_id,
                "branch": runtime(db_session).stock.branch_id,
            },
        )
    ).one()
    result = await finish(db_session, completion)
    saved = await RetailCheckoutOriginRepository(db_session).read(
        result.tenant_id, result.sale_id, result.operation_id
    )
    assert saved is not None
    assert saved.origin_node_id == writer.writer_node_id
    assert saved.writer_epoch == writer.writer_epoch
    assert (
        saved.tenant_id,
        saved.sale_id,
        saved.operation_id,
        saved.register_id,
        saved.cashier_id,
    ) == (
        result.tenant_id,
        result.sale_id,
        result.operation_id,
        result.register_id,
        result.cashier_id,
    )
    assert saved.source_kind == ("mixed" if path.startswith("mixed") else "physical")
    assert saved.created_by == result.cashier_id
    assert saved.created_at.tzinfo is not None
    assert await stream_facts(db_session) == before_stream
    before = await protected_facts(db_session)
    assert await read_origin(db_session, result) == saved
    assert await read_origin(db_session, result) == saved
    assert await finish(db_session, completion) == result
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_retail_checkout_origin WHERE sale_id=:sale"),
            {"sale": result.sale_id},
        )
        == 1
    )
    assert await read_origin(db_session, result) == saved
    assert await protected_facts(db_session) == before
    assert await stream_facts(db_session) == before_stream


@pytest.mark.parametrize("path", PATHS)
async def test_savepoint_rollback_removes_checkout_and_captured_origin_together(
    db_session: AsyncSession, path: PathKind
) -> None:
    completion = await prepare(db_session, path)
    before = await protected_facts(db_session)
    point = await db_session.begin_nested()
    result = await finish(db_session, completion)
    assert await read_origin(db_session, result) is not None
    await point.rollback()
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_retail_checkout_origin WHERE sale_id=:sale"),
            {"sale": result.sale_id},
        )
        == 0
    )
    table = "pos_mixed_checkout" if path.startswith("mixed") else "pos_physical_checkout"
    assert (
        await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE sale_id=:sale"), {"sale": result.sale_id}
        )
        == 0
    )
    assert await protected_facts(db_session) == before


async def test_origin_rls_is_cashier_and_tenant_scoped_and_service_keeps_branch_authority(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    other_actor = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            assert str(await support.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await support.scalar(text("SELECT current_user")) == "aurum_support"
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            foreign = await seed_ledger_context(support)
    foreign_actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    result = await finish(db_session, await prepare(db_session, "mixed-cash"))
    saved = await read_origin(db_session, result)
    assert saved is not None
    before = await protected_facts(db_session)
    try:
        with pytest.raises(PermissionDeniedError):
            await RetailCheckoutOriginService(RetailCheckoutOriginRepository(db_session)).read(
                tenant_id=result.tenant_id,
                sale_id=result.sale_id,
                operation_id=result.operation_id,
                actor_id=result.cashier_id,
                allowed_branch_ids=set(),
            )
        await set_receiving_context(db_session, context.stock, other_actor)
        assert (
            await RetailCheckoutOriginRepository(db_session).read(
                result.tenant_id, result.sale_id, result.operation_id
            )
            is None
        )
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM pos_retail_checkout_origin WHERE id=:operation"),
                {"operation": result.operation_id},
            )
            == 0
        )
        with pytest.raises(PermissionDeniedError):
            await RetailCheckoutOriginService(RetailCheckoutOriginRepository(db_session)).read(
                tenant_id=result.tenant_id,
                sale_id=result.sale_id,
                operation_id=result.operation_id,
                actor_id=other_actor,
                allowed_branch_ids={result.branch_id},
            )
        await set_receiving_context(db_session, foreign, foreign_actor)
        assert await db_session.scalar(text("SELECT session_user")) == "aurum_app"
        assert (
            await RetailCheckoutOriginRepository(db_session).read(
                result.tenant_id, result.sale_id, result.operation_id
            )
            is None
        )
        with pytest.raises(NotFoundError):
            await RetailCheckoutOriginService(RetailCheckoutOriginRepository(db_session)).read(
                tenant_id=foreign.tenant_id,
                sale_id=result.sale_id,
                operation_id=result.operation_id,
                actor_id=foreign_actor,
                allowed_branch_ids={foreign.branch_id},
            )
    finally:
        await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await read_origin(db_session, result) == saved
    assert await protected_facts(db_session) == before


async def test_direct_origin_dml_and_trigger_function_calls_are_not_runtime_capabilities(
    db_session: AsyncSession,
) -> None:
    result = await finish(db_session, await prepare(db_session, "physical-cash"))
    saved = await read_origin(db_session, result)
    # PERFORM reaches server-side EXECUTE checks without exposing the trigger
    # pseudo-type to asyncpg's result-type introspection.
    statements = (
        "INSERT INTO pos_retail_checkout_origin SELECT * "
        "FROM pos_retail_checkout_origin WHERE id=:id",
        "UPDATE pos_retail_checkout_origin SET writer_epoch=writer_epoch+1 WHERE id=:id",
        "DELETE FROM pos_retail_checkout_origin WHERE id=:id",
        "TRUNCATE pos_retail_checkout_origin",
        "DO $$ BEGIN PERFORM public.trg_capture_retail_checkout_origin(); END $$",
        "DO $$ BEGIN PERFORM public.trg_retail_checkout_origin_guard(); END $$",
    )
    before = await protected_facts(db_session)
    for statement in statements:
        with pytest.raises(DBAPIError) as denied:
            async with db_session.begin_nested():
                await db_session.execute(text(statement), {"id": result.operation_id})
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    assert await read_origin(db_session, result) == saved
    assert await protected_facts(db_session) == before


async def test_capture_keeps_writer_share_lock_until_the_checkout_transaction_ends(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    result = await finish(db_session, await prepare(db_session, "physical-cash"))
    async with maintenance_engine.begin() as observer:
        assert str(await observer.scalar(text("SELECT current_database()"))).endswith("_test")
        with pytest.raises(DBAPIError) as blocked:
            async with observer.begin_nested():
                await observer.execute(
                    text("""
                        SELECT writer_node_id FROM sync_stream
                        WHERE tenant_id=:tenant AND branch_id=:branch FOR UPDATE NOWAIT
                    """),
                    {"tenant": result.tenant_id, "branch": result.branch_id},
                )
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
    assert await read_origin(db_session, result) is not None


async def test_captured_origin_has_one_immutable_metadata_only_audit_entry(
    db_session: AsyncSession,
) -> None:
    result = await finish(db_session, await prepare(db_session, "mixed-electronic"))
    rows = (
        await db_session.execute(
            text("""
                SELECT record_id,action,old_values,new_values FROM audit_log
                WHERE table_name='pos_retail_checkout_origin'
                  AND tenant_id=:tenant AND record_id=:operation
            """),
            {"tenant": result.tenant_id, "operation": result.operation_id},
        )
    ).all()
    assert len(rows) == 1
    audit = rows[0]
    assert audit.record_id == result.operation_id and audit.action == "INSERT"
    assert audit.old_values is None
    assert audit.new_values["id"] == str(result.operation_id)
    assert audit.new_values["created_by"] == str(result.cashier_id)
    assert set(audit.new_values) <= {
        "id",
        "tenant_id",
        "source_kind",
        "sale_id",
        "branch_id",
        "register_id",
        "cashier_user_id",
        "physical_checkout_id",
        "mixed_checkout_id",
        "origin_node_id",
        "writer_epoch",
        "created_at",
        "created_by",
        "source_transaction_id",
        "event_capture_required",
    }
    assert "result_payload" not in audit.new_values and "receipt_payload" not in audit.new_values
