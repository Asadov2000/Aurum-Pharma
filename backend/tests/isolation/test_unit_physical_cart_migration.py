"""Physical drafts retain actual-cashier isolation and reversible legacy guards."""

from __future__ import annotations

import importlib.util
import re
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
from app.core.errors import PermissionDeniedError
from app.domains.pos.unit_cart_quote import UnitCartQuoteService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_unit_cash_intent import save
from tests.domains.pos.test_unit_physical_cart import physical_draft, physical_request, put_physical
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def migration(filename: str = "0179_unit_physical_cart_intents.py") -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location(f"physical_cart_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_physical_migration_restores_exact_legacy_guard_bodies(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            before = (
                connection.execute(
                    text("SELECT to_jsonb(row) FROM pos_unit_cart_item row ORDER BY tenant_id,id")
                )
                .scalars()
                .all()
            )
            if any(row["physical_packaging"] is not None for row in before):
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                return
            migration().downgrade()
            assert not connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE "
                    "table_schema='public' AND table_name='pos_unit_cart_item' "
                    "AND column_name='physical_packaging')"
                )
            )
            for filename, function in (
                ("0170_unit_cart_intents.py", "trg_unit_cart_guard"),
                ("0171_unit_cash_intents.py", "trg_unit_cash_intent_guard"),
            ):
                original = migration(filename).FUNCTIONS
                match = re.search(
                    rf"CREATE FUNCTION public\.{function}\(\).*?AS \$\$(.*?)\$\$;",
                    original,
                    flags=re.DOTALL,
                )
                assert match is not None
                restored = connection.scalar(
                    text("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:name)"),
                    {"name": f"public.{function}()"},
                )
                assert str(restored).strip() == match.group(1).strip()
            migration().upgrade()
            after = (
                connection.execute(
                    text("SELECT to_jsonb(row) FROM pos_unit_cart_item row ORDER BY tenant_id,id")
                )
                .scalars()
                .all()
            )
            assert after == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_downgrade_refuses_physical_history_without_changing_snapshot(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id, sale_format="blister")
    request = await physical_request(db_session, sale.id)
    await save(db_session, request)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            query = text("SELECT to_jsonb(row) FROM pos_unit_cart_item row WHERE id=:id")
            before = connection.scalar(query, {"id": item.id})
            assert before["physical_packaging"]["units_per_blister"] == 10
            with pytest.raises(DBAPIError) as error:
                with connection.begin_nested():
                    migration().downgrade()
            assert getattr(error.value.orig, "sqlstate", None) == "55000"
            assert connection.scalar(query, {"id": item.id}) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()


async def test_actual_cashier_tenant_and_branch_scope_physical_rows(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id)
    service = UnitCartQuoteService(UnitCartRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.quote(
            tenant_id=context.stock.tenant_id,
            sale_id=sale.id,
            actor_id=context.actor_id,
            allowed_branch_ids={uuid4()},
        )
    await set_receiving_context(db_session, context.stock, other)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cart_item WHERE id=:id"), {"id": item.id}
        )
        == 0
    )
    with pytest.raises(PermissionDeniedError):
        await put_physical(db_session, sale.id, position=2)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cart_item WHERE id=:id"), {"id": item.id}
        )
        == 0
    )


@pytest.mark.parametrize(
    "change",
    [
        "jsonb_set(quote_payload,'{lines,0,packaging,units_per_blister}','15')",
        "jsonb_set(quote_payload,'{lines,0,inventory_kind}','\"prepared_unit\"')",
        "jsonb_set(quote_payload,'{lines,0,base_units}','1')",
    ],
)
async def test_cash_guard_rejects_forged_physical_quote(
    db_session: AsyncSession,
    change: str,
) -> None:
    sale = await physical_draft(db_session)
    await put_physical(db_session, sale.id, sale_format="blister")
    request = await physical_request(db_session, sale.id)
    await save(db_session, request)
    columns = (
        "tenant_id,id,branch_id,sale_id,register_id,shift_id,cashier_user_id,request_hash,"
        "revision_hash,expected_total_amount,cash_tendered,change_amount,currency,"
        "quote_payload,created_by"
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO pos_unit_cash_intent({columns}) "
                    "SELECT tenant_id,:new,branch_id,sale_id,register_id,shift_id,"
                    "cashier_user_id,request_hash,revision_hash,expected_total_amount,"
                    f"cash_tendered,change_amount,currency,{change},created_by "
                    "FROM pos_unit_cash_intent WHERE id=:id"
                ),
                {"new": uuid4(), "id": request.operation_id},
            )
