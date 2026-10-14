"""Shift closure checks unit drafts across cashier RLS without granting access."""

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.errors import BusinessRuleError
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_physical_cart import physical_draft, put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
CLOSE = text("UPDATE public.shift SET status='closed' WHERE id=:shift RETURNING status")
CONSTRAINT = "ck_shift_unfinished_unit_cart"


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions"
    spec = importlib.util.spec_from_file_location(
        "unit_shift_close_migration", path / "0199_unit_draft_shift_close_guard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def assert_blocked(session: AsyncSession, shift_id: UUID) -> None:
    with pytest.raises(DBAPIError) as error:
        async with session.begin_nested():
            await session.execute(CLOSE, {"shift": shift_id})
    assert getattr(error.value.orig, "sqlstate", None) == "23514"
    assert getattr(error.value.orig.__cause__, "constraint_name", None) == CONSTRAINT
    assert (
        await session.scalar(
            text("SELECT status FROM public.shift WHERE id=:shift"), {"shift": shift_id}
        )
        == "open"
    )


@pytest.mark.parametrize("physical", [False, True])
async def test_direct_shift_close_rejects_own_unfinished_unit_items(
    db_session: AsyncSession, physical: bool
) -> None:
    sale = await seed_cart_draft(db_session)
    if physical:
        await put_physical(db_session, sale.id)
    else:
        await put_cart_item(db_session, sale.id)
    await assert_blocked(db_session, sale.shift_id)
    assert (
        await db_session.scalar(
            text("SELECT status FROM public.sale WHERE id=:sale"), {"sale": sale.id}
        )
        == "draft"
    )


async def test_shift_manager_cannot_bypass_other_cashier_cart_rls(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    manager = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id)
    await set_receiving_context(db_session, context.stock, manager)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM public.pos_unit_cart_item WHERE id=:item"), {"item": item.id}
        )
        == 0
    )
    await assert_blocked(db_session, sale.shift_id)
    # The restrictive trigger must not reveal the hidden cart after rejecting closure.
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM public.pos_unit_cart_item WHERE id=:item"), {"item": item.id}
        )
        == 0
    )


async def test_empty_and_removed_cart_shifts_can_close_independently(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    occupied = await physical_draft(db_session)
    item = await put_physical(db_session, occupied.id)
    empty = await seed_cart_draft(db_session)
    assert await db_session.scalar(CLOSE, {"shift": empty.shift_id}) == "closed"
    await assert_blocked(db_session, occupied.shift_id)
    await UnitCartService(UnitCartRepository(db_session)).remove_item(
        tenant_id=context.stock.tenant_id,
        sale_id=occupied.id,
        item_id=item.id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert await db_session.scalar(CLOSE, {"shift": occupied.shift_id}) == "closed"


async def test_owner_guard_and_other_tenant_empty_shift_are_independent(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    occupied = await physical_draft(db_session)
    await put_physical(db_session, occupied.id)
    await db_session.commit()
    async with AsyncSession(maintenance_engine) as owner:
        assert await owner.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        await assert_blocked(owner, occupied.shift_id)
    async with AsyncSession(db_engine) as seeder:
        await seeder.execute(text("SELECT set_config('app.support_session','true',true)"))
        other_stock = await seed_ledger_context(seeder)
        await seeder.commit()
    actor = await seed_receiving_actor(db_engine, maintenance_engine, other_stock)
    async with context.factory() as other:
        other.info["receiving_runtime"] = ReceivingRuntime(context.factory, other_stock, actor)
        await set_receiving_context(other, other_stock, actor)
        empty = await seed_cart_draft(other)
        assert await other.scalar(CLOSE, {"shift": empty.shift_id}) == "closed"


async def test_completed_physical_sale_retains_cart_and_allows_close(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    await complete(db_session, await saved_request(db_session, source.sale))
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM public.pos_unit_cart_item WHERE sale_id=:sale"),
            {"sale": source.sale.id},
        )
        == 1
    )
    assert await db_session.scalar(CLOSE, {"shift": source.sale.shift_id}) == "closed"


async def test_committed_cart_writer_blocks_waiting_shift_close(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    sale = await physical_draft(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await put_physical(db_session, sale.id)
    started = asyncio.Event()

    async def close_after_writer() -> str:
        async with context.factory() as closer:
            await set_receiving_context(closer, context.stock, context.actor_id)
            await closer.execute(text("SET LOCAL lock_timeout='5s'"))
            started.set()
            await assert_blocked(closer, sale.shift_id)
            return "blocked"

    task = asyncio.create_task(close_after_writer())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not task.done()
        await db_session.commit()
        assert await asyncio.wait_for(task, timeout=5) == "blocked"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_committed_shift_close_rejects_waiting_cart_writer(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    sale = await physical_draft(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await db_session.scalar(CLOSE, {"shift": sale.shift_id}) == "closed"
    started = asyncio.Event()

    async def edit_after_close() -> str:
        async with context.factory() as editor:
            editor.info["receiving_runtime"] = context
            await set_receiving_context(editor, context.stock, context.actor_id)
            await editor.execute(text("SET LOCAL lock_timeout='5s'"))
            started.set()
            with pytest.raises(BusinessRuleError):
                await put_physical(editor, sale.id)
            assert (
                await editor.scalar(
                    text("SELECT count(*) FROM public.pos_unit_cart_item WHERE sale_id=:sale"),
                    {"sale": sale.id},
                )
                == 0
            )
            return "blocked"

    task = asyncio.create_task(edit_after_close())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not task.done()
        await db_session.commit()
        assert await asyncio.wait_for(task, timeout=5) == "blocked"
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_guard_acl_and_downgrade_roundtrip_preserve_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        query = text("""
            SELECT pg_get_userbyid(proowner) AS owner,prosecdef,proconfig,
              has_function_privilege('aurum_app',oid,'EXECUTE') AS app_execute,
              has_function_privilege('aurum_support',oid,'EXECUTE') AS support_execute,
              EXISTS(SELECT 1 FROM aclexplode(proacl) acl WHERE acl.grantee=0) AS public_execute,
              pg_get_functiondef(oid) AS definition
            FROM pg_proc WHERE oid=to_regprocedure('public.trg_shift_unfinished_unit_cart()')
        """)
        before = connection.execute(query).mappings().one()
        assert before["owner"] == "aurum_schema_owner"
        assert before["prosecdef"]
        assert before["proconfig"] == ["search_path=pg_catalog, pg_temp"]
        assert not before["app_execute"] and not before["support_execute"]
        assert not before["public_execute"]
        assert "FOR UPDATE" not in before["definition"]
        history_query = text("""
            SELECT 'shift',count(*),sum(hashtextextended(to_jsonb(row)::text,0)::numeric)
              FROM public.shift row
            UNION ALL
            SELECT 'sale',count(*),sum(hashtextextended(to_jsonb(row)::text,0)::numeric)
              FROM public.sale row
            UNION ALL
            SELECT 'cart',count(*),sum(hashtextextended(to_jsonb(row)::text,0)::numeric)
              FROM public.pos_unit_cart_item row
        """)
        history = connection.execute(history_query).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert (
                connection.scalar(
                    text("SELECT to_regprocedure('public.trg_shift_unfinished_unit_cart()')")
                )
                is None
            )
            assert connection.execute(history_query).all() == history
            migration().upgrade()
        assert connection.execute(query).mappings().one() == before
        assert connection.execute(history_query).all() == history

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
