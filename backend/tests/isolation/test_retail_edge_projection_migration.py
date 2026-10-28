"""Owner-provisioned receive anchors stay immutable and narrowly scoped in CI."""

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
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.sync.test_retail_edge_apply_integration import edge_context, prepared_case

db_session = receiving_session_fixture
TABLE = "sync_retail_event_anchor"


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0227_retail_edge_projection.py"
    spec = importlib.util.spec_from_file_location("retail_edge_projection_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_receive_anchor_has_forced_rls_and_exact_read_only_runtime_acl(
    maintenance_engine: AsyncEngine,
) -> None:
    roles = (
        "aurum_app",
        "aurum_support",
        "aurum_worker",
        "aurum_catalog_worker",
        "aurum_billing_worker",
        "aurum_mailer",
        "aurum_edge_cash_owner",
        "aurum_edge_cash_executor",
    )
    async with maintenance_engine.connect() as connection:
        assert (
            await connection.execute(
                text(
                    "SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity "
                    "FROM pg_class WHERE oid='public.sync_retail_event_anchor'::regclass"
                )
            )
        ).one() == ("aurum_schema_owner", True, True)
        for role in roles:
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                allowed = await connection.scalar(
                    text("SELECT has_table_privilege(:role,:table,:privilege)"),
                    {"role": role, "table": f"public.{TABLE}", "privilege": privilege},
                )
                assert allowed is (role in {"aurum_app", "aurum_support"} and privilege == "SELECT")
            assert (
                await connection.scalar(
                    text(
                        "SELECT has_function_privilege(:role,"
                        "'public.trg_retail_edge_anchor_guard()','EXECUTE')"
                    ),
                    {"role": role},
                )
                is False
            )
        default = await connection.scalar(text("""
            SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
            JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
            WHERE d.adrelid='public.sync_sale_projection'::regclass
              AND a.attname='schema_version'
        """))
        assert default == "1"


async def test_anchor_requires_exact_tenant_branch_target_node_and_runtime_cannot_forge_it(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    case = await prepared_case(db_session, maintenance_engine, "mixed-cash")
    statement = text("SELECT count(*) FROM sync_retail_event_anchor WHERE event_id=:event")
    parameters = {"event": case.envelope.event_id}
    assert await db_session.scalar(statement, parameters) == 1
    for setting in ("app.tenant_id", "app.branch_id", "app.edge_node_id"):
        await db_session.execute(
            text("SELECT set_config(:setting,:value,true)"),
            {"setting": setting, "value": str(uuid4())},
        )
        assert await db_session.scalar(statement, parameters) == 0
        await edge_context(db_session, case)
    for sql in (
        "INSERT INTO sync_retail_event_anchor SELECT * FROM sync_retail_event_anchor",
        "UPDATE sync_retail_event_anchor SET event_fingerprint=repeat('f',64) "
        "WHERE event_id=:event",
        "DELETE FROM sync_retail_event_anchor WHERE event_id=:event",
        "TRUNCATE sync_retail_event_anchor",
    ):
        with pytest.raises(DBAPIError) as refused:
            async with db_session.begin_nested():
                await db_session.execute(text(sql), parameters)
        assert getattr(refused.value.orig, "sqlstate", None) == "42501"
    assert await db_session.scalar(statement, parameters) == 1
    async with maintenance_engine.connect() as owner:
        audit = (
            await owner.execute(
                text(
                    "SELECT action,new_values FROM audit_log WHERE tenant_id=:tenant "
                    "AND table_name='sync_retail_event_anchor' ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant": case.source.tenant_id},
            )
        ).one()
    assert audit.action == "INSERT"
    assert audit.new_values["event_id"] == str(case.envelope.event_id)
    assert not {"payload", "record", "items", "lines", "payments", "total_amount"} & set(
        audit.new_values
    )


async def test_owner_cannot_mutate_truncate_or_downgrade_existing_trust_history(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    case = await prepared_case(db_session, maintenance_engine, "physical-cash")
    async with maintenance_engine.connect() as owner:
        transaction = await owner.begin()
        try:
            for sql in (
                "UPDATE sync_retail_event_anchor SET sequence=sequence+1 WHERE event_id=:event",
                "DELETE FROM sync_retail_event_anchor WHERE event_id=:event",
                "TRUNCATE sync_retail_event_anchor",
            ):
                point = await owner.begin_nested()
                try:
                    with pytest.raises(DBAPIError, match="retail_edge_anchor_immutable") as refused:
                        await owner.execute(text(sql), {"event": case.envelope.event_id})
                    assert getattr(refused.value.orig, "sqlstate", None) == "55000"
                finally:
                    await point.rollback()

            def refuse_downgrade(connection: Connection) -> None:
                assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
                assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
                with Operations.context(MigrationContext.configure(connection)):
                    migration().downgrade()

            point = await owner.begin_nested()
            try:
                with pytest.raises(
                    DBAPIError, match="retail_edge_downgrade_requires_empty_history"
                ):
                    await owner.run_sync(refuse_downgrade)
            finally:
                await point.rollback()
        finally:
            await transaction.rollback()


async def test_empty_migration_roundtrip_or_protected_history_refusal_preserves_head(
    maintenance_engine: AsyncEngine,
) -> None:
    module = migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        has_history = connection.scalar(text("""
            SELECT EXISTS(SELECT 1 FROM sync_retail_event_anchor)
                OR EXISTS(SELECT 1 FROM sync_sale_projection WHERE schema_version<>1)
        """))
        with Operations.context(MigrationContext.configure(connection)):
            if has_history:
                point = connection.begin_nested()
                try:
                    with pytest.raises(
                        DBAPIError, match="retail_edge_downgrade_requires_empty_history"
                    ):
                        module.downgrade()
                finally:
                    point.rollback()
            else:
                module.downgrade()
                assert (
                    connection.scalar(text("SELECT to_regclass('public.sync_retail_event_anchor')"))
                    is None
                )
                module.upgrade()
            assert (
                connection.scalar(text("SELECT to_regclass('public.sync_retail_event_anchor')"))
                is not None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname="
                        "'ck_sync_sale_projection_version')"
                    )
                )
                is True
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
