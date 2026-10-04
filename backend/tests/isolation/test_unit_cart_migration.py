"""Actual runtime-role boundaries for unpublished unit cart drafts."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.errors import BusinessRuleError, ConflictError
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def migration(filename: str = "0170_unit_cart_intents.py") -> ModuleType:
    source = Path(__file__).resolve().parents[2] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location(f"unit_cart_{source.stem}", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_unit_cart_empty_downgrade_roundtrip(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_unit_cart_item)")):
                with pytest.raises(DBAPIError, match="unit_cart_nonempty_downgrade_forbidden"):
                    with connection.begin_nested():
                        migration().downgrade()
                return
            # Rehearse the actual dependency order: 0172 adds a scoped FK to
            # unit cart rows even when both tables contain no unit intents.
            migration("0172_unit_prescription_links.py").downgrade()
            migration().downgrade()
            assert connection.scalar(text("SELECT to_regclass('pos_unit_cart_item')")) is None
            migration().upgrade()
            migration("0172_unit_prescription_links.py").upgrade()
            assert connection.scalar(text("""
                SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
                WHERE oid='pos_unit_cart_item'::regclass
            """))

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("path", ["complete", "cash", "card", "delete", "move"])
async def test_database_closes_legacy_financial_paths(db_session: AsyncSession, path: str) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    await put_cart_item(db_session, sale.id)
    statements = {
        "complete": "UPDATE sale SET status='completed' WHERE id=:sale",
        "cash": "INSERT INTO sale_payment(id,tenant_id,sale_id,payment_method,amount,currency) "
        "VALUES(:id,:tenant,:sale,'cash',1,'TJS')",
        "card": "INSERT INTO pos_payment_attempt(id,tenant_id,sale_id,cashier_user_id,"
        "operation_id,operation_hash,payment_method,amount) "
        "VALUES(:id,:tenant,:sale,:actor,:operation,:hash,'card',1)",
        "delete": "DELETE FROM sale WHERE id=:sale",
        "move": "UPDATE sale SET cashier_user_id=:other WHERE id=:sale",
    }
    with pytest.raises(DBAPIError, match="unit_cart_checkout_not_ready"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(statements[path]),
                {
                    "id": uuid4(),
                    "tenant": context.stock.tenant_id,
                    "sale": sale.id,
                    "actor": context.actor_id,
                    "operation": uuid4(),
                    "hash": "a" * 64,
                    "other": uuid4(),
                },
            )
    assert (
        await db_session.scalar(text("SELECT status FROM sale WHERE id=:sale"), {"sale": sale.id})
        == "draft"
    )


@pytest.mark.parametrize("change", ["quantity", "identity", "discount"])
async def test_raw_writes_cannot_bypass_cart_invariants(
    db_session: AsyncSession, change: str
) -> None:
    sale = await seed_cart_draft(db_session, step=2)
    item_id = await put_cart_item(db_session, sale.id, quantity=2)
    clause, expected = {
        "quantity": ("quantity=3", "unit_cart_quantity_invalid"),
        "identity": ("catalog_id=:other", "unit_cart_identity_immutable"),
        "discount": ("discount_amount=999", "unit_cart_discount_invalid"),
    }[change]
    with pytest.raises(DBAPIError, match=expected):
        async with db_session.begin_nested():
            await db_session.execute(
                text(f"UPDATE pos_unit_cart_item SET {clause} WHERE id=:id"),
                {"id": item_id, "other": uuid4()},
            )


async def test_draft_is_private_to_actual_cashier_and_tenant(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    sale = await seed_cart_draft(db_session)
    item_id = await put_cart_item(db_session, sale.id)
    await set_receiving_context(db_session, context.stock, other)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cart_item WHERE id=:id"),
            {"id": item_id},
        )
        == 0
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("""
                INSERT INTO pos_unit_cart_item(id,tenant_id,branch_id,sale_id,catalog_id,
                  position,sale_format,quantity)
                VALUES(:id,:tenant,:branch,:sale,:catalog,2,'unit',1)
            """),
                {
                    "id": uuid4(),
                    "tenant": context.stock.tenant_id,
                    "branch": context.stock.branch_id,
                    "sale": sale.id,
                    "catalog": context.stock.catalog_id,
                },
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"),
        {"tenant": str(uuid4())},
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cart_item WHERE id=:id"),
            {"id": item_id},
        )
        == 0
    )


async def test_payment_started_prevents_cart_creation(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    await db_session.execute(
        text(
            "INSERT INTO sale_payment(id,tenant_id,sale_id,payment_method,amount) "
            "VALUES(:id,:tenant,:sale,'cash',1)"
        ),
        {"id": uuid4(), "tenant": context.stock.tenant_id, "sale": sale.id},
    )
    with pytest.raises(DBAPIError, match="unit_cart_payment_started"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("""
                INSERT INTO pos_unit_cart_item(id,tenant_id,branch_id,sale_id,catalog_id,
                  position,sale_format,quantity)
                VALUES(:id,:tenant,:branch,:sale,:catalog,1,'unit',1)
            """),
                {
                    "id": uuid4(),
                    "tenant": context.stock.tenant_id,
                    "branch": context.stock.branch_id,
                    "sale": sale.id,
                    "catalog": context.stock.catalog_id,
                },
            )


async def test_removing_last_intent_restores_legacy_payment_path(
    db_session: AsyncSession,
) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    item_id = await put_cart_item(db_session, sale.id)
    await db_session.execute(text("DELETE FROM pos_unit_cart_item WHERE id=:id"), {"id": item_id})
    await db_session.execute(
        text(
            "INSERT INTO sale_payment(id,tenant_id,sale_id,payment_method,amount) "
            "VALUES(:id,:tenant,:sale,'cash',1)"
        ),
        {"id": uuid4(), "tenant": context.stock.tenant_id, "sale": sale.id},
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM sale_payment WHERE sale_id=:sale"),
            {"sale": sale.id},
        )
        == 1
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM audit_log WHERE table_name='pos_unit_cart_item' "
                "AND record_id=:id"
            ),
            {"id": item_id},
        )
        == 2
    )


async def test_nonempty_cart_blocks_downgrade(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    sale = await seed_cart_draft(db_session)
    await put_cart_item(db_session, sale.id)
    await db_session.commit()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="unit_cart_nonempty_downgrade_forbidden"):
                with connection.begin_nested():
                    migration().downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("leader", ["cart", "pay"])
async def test_concurrent_cart_and_payment_cannot_both_commit(
    db_session: AsyncSession, leader: str
) -> None:
    sale = await seed_cart_draft(db_session)
    context = runtime(db_session)
    await db_session.commit()
    leader_ready, contender_started = asyncio.Event(), asyncio.Event()

    async def take_turn(session: AsyncSession, kind: str) -> None:
        if kind == leader:
            await session.execute(
                text("SELECT id FROM sale WHERE id=:sale FOR UPDATE"), {"sale": sale.id}
            )
            leader_ready.set()
            await contender_started.wait()
        else:
            await leader_ready.wait()
            contender_started.set()

    async def cart() -> bool:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            session.info["receiving_runtime"] = context
            try:
                await take_turn(session, "cart")
                await put_cart_item(session, sale.id)
                await session.commit()
                return True
            except (BusinessRuleError, ConflictError):
                await session.rollback()
                return False
            except DBAPIError as error:
                assert "unit_cart_" in str(error.orig)
                await session.rollback()
                return False

    async def pay() -> bool:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            try:
                await take_turn(session, "pay")
                await session.execute(
                    text(
                        "INSERT INTO sale_payment(id,tenant_id,sale_id,payment_method,amount) "
                        "VALUES(:id,:tenant,:sale,'cash',1)"
                    ),
                    {"id": uuid4(), "tenant": context.stock.tenant_id, "sale": sale.id},
                )
                await session.commit()
                return True
            except DBAPIError as error:
                assert "unit_cart_checkout_not_ready" in str(error.orig)
                await session.rollback()
                return False

    outcomes = await asyncio.wait_for(asyncio.gather(cart(), pay()), timeout=15)
    assert outcomes == ([True, False] if leader == "cart" else [False, True])
    await set_receiving_context(db_session, context.stock, context.actor_id)
    counts = await db_session.execute(
        text("""
        SELECT (SELECT count(*) FROM pos_unit_cart_item WHERE sale_id=:sale),
               (SELECT count(*) FROM sale_payment WHERE sale_id=:sale)
    """),
        {"sale": sale.id},
    )
    assert tuple(counts.one()) in ((1, 0), (0, 1))
