"""Unit prescription downgrade checks, exclusively in the disposable test DB."""

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
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0172_unit_prescription_links.py"
    spec = importlib.util.spec_from_file_location("unit_prescription_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_empty_unit_prescription_downgrade_roundtrip(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        if connection.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM prescription_log WHERE unit_cart_item_id IS NOT NULL)"
            )
        ):
            pytest.skip("Immutable unit prescription history prevents an empty downgrade rehearsal")
        security_before = connection.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity,relacl::text FROM pg_class "
                "WHERE oid='public.prescription_log'::regclass"
            )
        ).one()
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert not connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_attribute "
                    "WHERE attrelid='public.prescription_log'::regclass "
                    "AND attname='unit_cart_item_id' AND NOT attisdropped)"
                )
            )
            assert (
                connection.scalar(text("SELECT to_regprocedure('trg_unit_prescription_guard()')"))
                is None
            )
            migration().upgrade()
            constraints = (
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid IN "
                        "('public.prescription_log'::regclass,"
                        "'public.pos_unit_cart_item'::regclass) "
                        "AND conname IN ('fk_prescription_unit_cart_item',"
                        "'ck_prescription_item_kind','uq_unit_cart_prescription_scope')"
                    )
                )
                .scalars()
                .all()
            )
            assert set(constraints) == {
                "fk_prescription_unit_cart_item",
                "ck_prescription_item_kind",
                "uq_unit_cart_prescription_scope",
            }
            assert (
                connection.execute(
                    text(
                        "SELECT relrowsecurity,relforcerowsecurity,relacl::text FROM pg_class "
                        "WHERE oid='public.prescription_log'::regclass"
                    )
                ).one()
                == security_before
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger "
                        "WHERE tgrelid='prescription_log'::regclass "
                        "AND tgname IN ('a10_unit_prescription_guard',"
                        "'trg_guard_prescription_log_immutability') AND tgenabled='O'"
                    )
                )
                == 2
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_unit_prescription_nonempty_downgrade_is_rejected(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    sale = await seed_cart_draft(db_session)
    item_id = await put_cart_item(db_session, sale.id)
    prescription_id = uuid4()
    await db_session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO prescription_log(id,tenant_id,sale_id,unit_cart_item_id,notes) "
            "VALUES(:id,:tenant,:sale,:item,'Migration rehearsal')"
        ),
        {
            "id": prescription_id,
            "tenant": context.stock.tenant_id,
            "sale": sale.id,
            "item": item_id,
        },
    )
    await db_session.commit()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="unit_prescription_nonempty_downgrade_forbidden"):
                with connection.begin_nested():
                    migration().downgrade()
            assert (
                connection.scalar(
                    text("SELECT unit_cart_item_id FROM prescription_log WHERE id=:id"),
                    {"id": prescription_id},
                )
                == item_id
            )
            assert (
                connection.scalar(text("SELECT to_regprocedure('trg_unit_prescription_guard()')"))
                is not None
            )

    try:
        async with maintenance_engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.run_sync(rehearse)
            finally:
                await transaction.rollback()
    finally:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        await db_session.execute(
            text("DELETE FROM prescription_log WHERE id=:id"), {"id": prescription_id}
        )
        await db_session.commit()
