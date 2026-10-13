"""Historical v2 projection enforces actual identity and leaves source privileges intact."""

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_cost_ledger_v2 import priced_source
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense
from tests.domains.pos.test_unit_return import runtime
from tests.platform_access_helpers import create_test_platform_user

db_session = receiving_session_fixture
SIGNATURE = "public.pos_stock_on_date_for_report(uuid,timestamptz,uuid)"
READ_SQL = text("SELECT * FROM public.pos_stock_on_date_for_report(:tenant,:cutoff,:branch)")


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0196_stock_on_date_projection.py"
    spec = importlib.util.spec_from_file_location("stock_on_date_projection_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_physical_history_uses_exclusive_movement_cutoff_not_current_lot_counters(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session, quantity=2)
    posted = await dispense(db_session, source, (cart_line(source, "unit", 1),))
    first_spend = await db_session.scalar(
        text("SELECT min(created_at) FROM unit_stock_movement_v2 WHERE command_id=:id"),
        {"id": posted.command_id},
    )
    assert isinstance(first_spend, datetime)
    params = {
        "tenant": source.incoming.stock.tenant_id,
        "branch": source.incoming.stock.branch_id,
        "cutoff": first_spend,
    }
    old = (await db_session.execute(READ_SQL, params)).mappings().one()
    new = (
        await db_session.execute(
            READ_SQL, {**params, "cutoff": datetime.now(UTC) + timedelta(seconds=1)}
        )
    ).mappings().one()
    assert old["physical_stock"]["closed_packs"] == "2"
    assert old["physical_stock"]["opened_units"] == "0"
    assert old["physical_stock"]["base_units"] == "60"
    assert old["value"] == Decimal("20.00")
    assert new["physical_stock"]["closed_packs"] == "1"
    assert new["physical_stock"]["opened_units"] == "29"
    assert new["physical_stock"]["base_units"] == "59"
    assert new["value"] == Decimal("19.67")
    assert old["qty"] == new["qty"] == Decimal("0")


async def test_projection_owner_acl_stability_and_roundtrip_do_not_change_raw_tables(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        metadata = (
            connection.execute(
                text("""
                    SELECT pg_get_userbyid(proowner) AS owner,prosecdef,
                      provolatile::TEXT,proconfig,pronargdefaults,
                      has_function_privilege('aurum_app',oid,'EXECUTE') AS app_execute,
                      has_function_privilege('aurum_support',oid,'EXECUTE') AS support_execute,
                      EXISTS(SELECT 1 FROM aclexplode(proacl) acl
                        WHERE acl.grantee=0 OR acl.is_grantable) AS excessive_access
                    FROM pg_proc WHERE oid=to_regprocedure(:signature)
                """),
                {"signature": SIGNATURE},
            )
            .mappings()
            .one()
        )
        assert metadata["owner"] == "aurum_schema_owner" and metadata["prosecdef"] is True
        assert metadata["provolatile"] == "s"
        assert metadata["proconfig"] == ["search_path=pg_catalog, pg_temp"]
        assert metadata["pronargdefaults"] == 1
        assert metadata["app_execute"] and metadata["support_execute"]
        assert not metadata["excessive_access"]
        raw = text("""
            SELECT relname,relacl,relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE relnamespace='public'::regnamespace AND relname IN (
              'batch','batch_movement','incoming_document','incoming_item','unit_stock_lot_v2',
              'unit_stock_entry_v2','unit_stock_movement_v2','unit_stock_opened_pack_v2')
            ORDER BY relname
        """)
        before = connection.execute(raw).all()
        count = connection.scalar(text("SELECT count(*) FROM unit_stock_movement_v2"))
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert connection.scalar(text("SELECT to_regprocedure(:s)"), {"s": SIGNATURE}) is None
            assert connection.execute(raw).all() == before
            migration().upgrade()
        assert connection.execute(raw).all() == before
        assert connection.scalar(text("SELECT count(*) FROM unit_stock_movement_v2")) == count

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize(
    "denial", ["foreign_tenant", "wrong_branch", "missing_actor", "no_reports", "fake_support"]
)
async def test_actual_app_projection_fails_closed_without_expanding_source_access(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    denial: str,
) -> None:
    context = runtime(db_session)
    # Creating membership in a second connection must precede tenant stock locks.
    restricted = (
        await seed_receiving_actor(
            db_engine, maintenance_engine, context.stock, permissions=("pos.sell",)
        )
        if denial == "no_reports"
        else None
    )
    source = await priced_source(db_session)
    params = {
        "tenant": context.stock.tenant_id,
        "branch": context.stock.branch_id,
        "cutoff": datetime.now(UTC) + timedelta(seconds=1),
    }
    assert len((await db_session.execute(READ_SQL, params)).all()) == 1
    if denial == "foreign_tenant":
        async with AsyncSession(db_engine) as support:
            async with support.begin():
                await support.execute(text("SELECT set_config('app.support_session','true',true)"))
                foreign = await seed_ledger_context(support)
        actor = await seed_receiving_actor(
            db_engine, maintenance_engine, foreign, permissions=("reports.view",)
        )
        await set_receiving_context(db_session, foreign, actor)
    elif denial in {"wrong_branch", "fake_support"}:
        params["branch"] = uuid4()
        if denial == "fake_support":
            await db_session.execute(text("SELECT set_config('app.support_session','true',true)"))
    elif denial == "missing_actor":
        await db_session.execute(text("SELECT set_config('app.user_id','',true)"))
    else:
        assert restricted is not None
        await set_receiving_context(db_session, context.stock, restricted)
    try:
        assert (await db_session.execute(READ_SQL, params)).all() == []
    finally:
        await db_session.execute(text("SELECT set_config('app.support_session','false',true)"))
        await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await db_session.scalar(
        text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE id=:id"), {"id": source.lot_id}
    ) == 1


async def test_real_support_login_with_active_platform_capability_reads_physical_balance(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await priced_source(db_session, quantity=2)
    context = runtime(db_session)
    params = {
        "tenant": context.stock.tenant_id,
        "branch": context.stock.branch_id,
        "cutoff": datetime.now(UTC) + timedelta(seconds=1),
    }
    expected = (await db_session.execute(READ_SQL, params)).mappings().all()
    # Publish accepted stock and release tenant locks before another connection
    # creates the protected platform account. Restore the application context.
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            assert await support.scalar(text("SELECT session_user")) == "aurum_support"
            assert await support.scalar(text("SELECT current_user")) == "aurum_support"
            actor = await create_test_platform_user(support, access_kind="developer")
            await support.execute(
                text(
                    "SELECT set_config('app.support_session','true',true),"
                    "set_config('app.user_id',:actor,true),"
                    "set_config('app.tenant_id',:tenant,true),"
                    "set_config('app.support_access_session_id','',true),"
                    "set_config('app.auth_session_id','',true)"
                ),
                {"actor": str(actor.id), "tenant": str(context.stock.tenant_id)},
            )
            assert await support.scalar(text("SELECT public.is_support_session()")) is True
            actual = (await support.execute(READ_SQL, params)).mappings().all()
            assert actual == expected and len(actual) == 1
            assert actual[0]["stock_id"] == source.lot_id
            assert actual[0]["physical_stock"]["base_units"] == "60"
            assert actual[0]["cost_status"] == "known"
            assert actual[0]["value"] == Decimal("20")
            assert (await support.execute(READ_SQL, {**params, "tenant": uuid4()})).all() == []
