"""Product-report projection exposes scoped retail totals without journal privileges."""

import importlib.util
from datetime import UTC, datetime, timedelta
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
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_return_journal import mixed_source
from tests.domains.pos.test_unit_return import runtime
from tests.platform_access_helpers import create_test_platform_user

db_session = receiving_session_fixture
SIGNATURE = "public.pos_product_totals_for_report(uuid,timestamptz,timestamptz,uuid)"
RAW_TABLES = (
    "sale",
    "sale_item",
    "pos_physical_checkout",
    "pos_mixed_checkout",
    "unit_stock_cart_command_v2",
    "unit_stock_movement_v2",
    "pos_physical_return_command",
    "pos_mixed_return_command",
    "pos_physical_zero_return",
    "pos_mixed_zero_return",
)
READ_SQL = text("SELECT * FROM public.pos_product_totals_for_report(:tenant,:start,:end,:branch)")


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0195_unit_product_report.py"
    spec = importlib.util.spec_from_file_location("unit_product_report_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_projection_has_fixed_owner_stability_search_path_and_narrow_execute_acl(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""
                        SELECT pg_get_userbyid(proowner) AS owner,prosecdef,
                          provolatile::TEXT,proconfig,pronargdefaults,
                          has_function_privilege('aurum_app',oid,'EXECUTE') AS app_execute,
                          has_function_privilege('aurum_support',oid,'EXECUTE') AS support_execute,
                          has_function_privilege('aurum_app',oid,
                            'EXECUTE WITH GRANT OPTION') AS app_grantable,
                          has_function_privilege('aurum_support',oid,
                            'EXECUTE WITH GRANT OPTION') AS support_grantable,
                          EXISTS(SELECT 1 FROM aclexplode(proacl) acl
                            WHERE acl.grantee=0) AS public_access
                        FROM pg_proc WHERE oid=to_regprocedure(:signature)
                    """),
                    {"signature": SIGNATURE},
                )
            )
            .mappings()
            .one()
        )
    assert row["owner"] == "aurum_schema_owner" and row["prosecdef"] is True
    assert row["provolatile"] == "s" and row["proconfig"] == ["search_path=pg_catalog, pg_temp"]
    assert row["pronargdefaults"] == 1
    assert row["app_execute"] and row["support_execute"]
    assert not row["app_grantable"] and not row["support_grantable"] and not row["public_access"]


async def test_projection_roundtrip_keeps_history_raw_acls_rls_and_other_functions(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        tables = text("""
            SELECT relname,relacl,relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE relnamespace='public'::regnamespace AND relname=ANY(:tables)
            ORDER BY relname
        """)
        definitions = text("""
            SELECT proname,pg_get_functiondef(oid),proacl,proowner FROM pg_proc
            WHERE pronamespace='public'::regnamespace AND proname IN (
              'audit_redact_table_snapshot','trg_mixed_cash_refund_guard',
              'trg_unit_return_guard','unit_lot_is_received_for_report') ORDER BY oid
        """)
        before_tables = connection.execute(tables, {"tables": list(RAW_TABLES)}).all()
        before_functions = connection.execute(definitions).all()
        before_counts = tuple(
            connection.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in RAW_TABLES
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert connection.scalar(text("SELECT to_regprocedure(:s)"), {"s": SIGNATURE}) is None
            assert connection.execute(tables, {"tables": list(RAW_TABLES)}).all() == before_tables
            migration().upgrade()
        assert connection.execute(tables, {"tables": list(RAW_TABLES)}).all() == before_tables
        assert connection.execute(definitions).all() == before_functions
        assert (
            tuple(
                connection.scalar(text(f"SELECT count(*) FROM public.{table}"))
                for table in RAW_TABLES
            )
            == before_counts
        )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize(
    "denial",
    [
        "tenant",
        "branch",
        "fake_support_branch",
        "missing_actor",
        "permission",
        "empty_range",
        "inverted_range",
    ],
)
async def test_projection_fails_closed_under_real_app_identity(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    denial: str,
) -> None:
    context = runtime(db_session)
    # Seed access before checkout holds the tenant's transaction lock.
    limited_actor = (
        await seed_receiving_actor(
            db_engine, maintenance_engine, context.stock, permissions=("pos.sell",)
        )
        if denial == "permission"
        else None
    )
    source = await mixed_source(db_session, fractional_legacy=True)
    now = datetime.now(UTC)
    params = {
        "tenant": source.original.tenant_id,
        "branch": source.original.branch_id,
        "start": now - timedelta(days=1),
        "end": now + timedelta(days=1),
    }
    assert len((await db_session.execute(READ_SQL, params)).all()) == 1
    if denial == "tenant":
        async with AsyncSession(db_engine) as support:
            async with support.begin():
                await support.execute(text("SELECT set_config('app.support_session','true',true)"))
                foreign = await seed_ledger_context(support)
        actor = await seed_receiving_actor(
            db_engine, maintenance_engine, foreign, permissions=("reports.view",)
        )
        await set_receiving_context(db_session, foreign, actor)
    elif denial in {"branch", "fake_support_branch"}:
        params["branch"] = uuid4()
        if denial == "fake_support_branch":
            await db_session.execute(text("SELECT set_config('app.support_session','true',true)"))
    elif denial == "missing_actor":
        await db_session.execute(text("SELECT set_config('app.user_id','',true)"))
    elif denial == "permission":
        assert limited_actor is not None
        await set_receiving_context(db_session, context.stock, limited_actor)
    elif denial == "empty_range":
        params["end"] = params["start"]
    else:
        params["end"] = now - timedelta(days=2)
    try:
        assert (await db_session.execute(READ_SQL, params)).all() == []
    finally:
        await db_session.execute(text("SELECT set_config('app.support_session','false',true)"))
        await set_receiving_context(db_session, context.stock, context.actor_id)


async def test_real_support_login_with_active_platform_capability_can_read_scoped_totals(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    now = datetime.now(UTC)
    params = {
        "tenant": source.original.tenant_id,
        "branch": source.original.branch_id,
        "start": now - timedelta(days=1),
        "end": now + timedelta(days=1),
    }
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
                {"actor": str(actor.id), "tenant": str(source.original.tenant_id)},
            )
            assert await support.scalar(text("SELECT public.is_support_session()")) is True
            assert (
                await support.scalar(
                    text(
                        "SELECT public.tenant_actor_has_scoped_permission("
                        ":tenant,'reports.view',:branch)"
                    ),
                    {"tenant": source.original.tenant_id, "branch": source.original.branch_id},
                )
                is True
            )
            rows = (await support.execute(READ_SQL, params)).mappings().all()
            assert len(rows) == 1
            assert rows[0]["quantity"] == source.original.lines[0].quantity
            assert rows[0]["revenue"] == source.original.total_amount
            assert rows[0]["physical_quantities"] == [{"unit": "tablet", "quantity": "60"}]
            assert (await support.execute(READ_SQL, {**params, "tenant": uuid4()})).all() == []
