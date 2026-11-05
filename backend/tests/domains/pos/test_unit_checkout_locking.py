"""Composed quote/preparation calls cannot deadlock on shared catalog upgrades."""

import asyncio
from dataclasses import replace
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import ConflictError
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_cart_quote import quote
from tests.domains.pos.test_unit_cash_intent import save
from tests.domains.pos.test_unit_checkout_preparation import prepare, request_for_cart
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def test_two_inherited_quote_locks_fail_fast_without_checkout_writes(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    # Separate actor setup commits role evidence; do it before any cart/source
    # transaction holds mutable catalog locks.
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    requests: list[tuple[UUID, CashConfirmationRequestV2]] = []
    for actor in (context.actor_id, other):
        db_session.info["receiving_runtime"] = replace(context, actor_id=actor)
        await set_receiving_context(db_session, context.stock, actor)
        sale = await seed_cart_draft(db_session)
        await put_cart_item(db_session, sale.id)
        request = await request_for_cart(db_session, sale.id)
        await save(db_session, request)
        requests.append((actor, request))
        await db_session.commit()
    db_session.info["receiving_runtime"] = context
    await set_receiving_context(db_session, context.stock, context.actor_id)
    tables = (
        "unit_stock_entry",
        "unit_stock_operation",
        "pos_unit_sale_line",
        "pos_unit_sale_command",
        "sale_payment",
        "pos_payment_attempt",
        "pos_unit_cash_intent",
        "audit_log",
    )
    counts_before = {
        table: await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        for table in tables
    }
    barrier = asyncio.Barrier(2)

    async def check(actor: UUID, request: CashConfirmationRequestV2) -> bool:
        async with context.factory() as session:
            session.info["receiving_runtime"] = replace(context, actor_id=actor)
            await set_receiving_context(session, context.stock, actor)
            await quote(session, request.sale_id)
            await barrier.wait()
            try:
                await prepare(session, request)
            except ConflictError as error:
                assert error.message == "Checkout stock is busy; retry in a new transaction"
                # NOWAIT errors were rolled back inside the preparation
                # savepoint, so the caller transaction is not aborted.
                assert await session.scalar(text("SELECT 1")) == 1
                return True
            finally:
                await session.rollback()
            return False

    outcomes = await asyncio.wait_for(
        asyncio.gather(*(check(actor, request) for actor, request in requests)), timeout=10
    )
    assert any(outcomes)
    for table, count in counts_before.items():
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == count
        )
