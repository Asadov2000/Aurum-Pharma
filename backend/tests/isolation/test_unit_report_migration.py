"""Report read policies grant scoped reads without stock writes or journal mutation."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.inventory.test_unit_report import prepare_report_lot
from tests.domains.pos.test_received_unit_line import seed_received

db_session = receiving_session_fixture


def _migration() -> ModuleType:
    source = Path(__file__).resolve().parents[2] / "alembic/versions/0168_unit_stock_report_read.py"
    spec = importlib.util.spec_from_file_location("unit_stock_report_read_migration", source)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_report_policy_migration_roundtrip_preserves_existing_history(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        original_counts = tuple(
            connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("unit_stock_lot", "unit_stock_entry")
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert (
                connection.scalar(
                    text(
                        "SELECT to_regprocedure('public.unit_lot_is_received_for_report("
                        "uuid,uuid,uuid,uuid,timestamp with time zone)')"
                    )
                )
                is None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
                        "AND tablename IN ('unit_stock_lot','unit_stock_entry') "
                        "AND policyname='tenant_report_read'"
                    )
                )
                == 0
            )
            assert (
                tuple(
                    connection.scalar(text(f"SELECT count(*) FROM {table}"))
                    for table in ("unit_stock_lot", "unit_stock_entry")
                )
                == original_counts
            )
            migration.upgrade()
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
                        "AND tablename IN ('unit_stock_lot','unit_stock_entry') "
                        "AND policyname='tenant_report_read' AND cmd='SELECT' "
                        "AND roles=ARRAY['aurum_app']::name[]"
                    )
                )
                == 2
            )
            signature = (
                "public.unit_lot_is_received_for_report("
                "uuid,uuid,uuid,uuid,timestamp with time zone)"
            )
            assert (
                connection.scalar(
                    text("SELECT has_function_privilege('aurum_app',:signature,'EXECUTE')"),
                    {"signature": signature},
                )
                is True
            )
            assert (
                connection.scalar(
                    text("SELECT has_function_privilege('aurum_support',:signature,'EXECUTE')"),
                    {"signature": signature},
                )
                is False
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_report_only_assignment_reads_own_branch_history_and_cannot_insert_or_update(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    prepared = await prepare_report_lot(db_session)
    await db_session.commit()
    reader = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        runtime.stock,
        permissions=("reports.view",),
    )
    await set_receiving_context(db_session, runtime.stock, reader)
    assert await db_session.scalar(text("SELECT current_user")) == "aurum_app"
    for table in ("unit_stock_lot", "unit_stock_entry"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": runtime.stock.tenant_id},
            )
            == 1
        )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO unit_stock_lot "
                    "(id,tenant_id,branch_id,catalog_id,unit,units_per_pack,"
                    "dispense_step,expires_at) "
                    "VALUES (:id,:tenant,:branch,:catalog,'tablet',30,1,CURRENT_DATE+180)"
                ),
                {
                    "id": uuid4(),
                    "tenant": runtime.stock.tenant_id,
                    "branch": runtime.stock.branch_id,
                    "catalog": runtime.stock.catalog_id,
                },
            )
    updated = await db_session.execute(
        text("UPDATE unit_stock_lot SET closed_packs=99 WHERE id=:id RETURNING id"),
        {"id": prepared.lot_id},
    )
    assert updated.scalars().all() == []
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id=:id"),
            {"id": prepared.lot_id},
        )
        == 2
    )


async def test_report_only_reader_cannot_select_other_branch_or_tenant_history(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    await prepare_report_lot(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            branch_id = await support.scalar(
                text(
                    "INSERT INTO branch (tenant_id,name) "
                    "VALUES (:tenant,'Report foreign branch') RETURNING id"
                ),
                {"tenant": runtime.stock.tenant_id},
            )
            assert isinstance(branch_id, UUID)
            foreign_tenant = await seed_ledger_context(support)
    for stock in (
        LedgerContext(runtime.stock.tenant_id, branch_id, runtime.stock.catalog_id),
        foreign_tenant,
    ):
        actor = await seed_receiving_actor(db_engine, maintenance_engine, stock)
        await set_receiving_context(db_session, stock, actor)
        db_session.info["receiving_runtime"] = ReceivingRuntime(runtime.factory, stock, actor)
        await prepare_report_lot(db_session)
        await db_session.commit()
    db_session.info["receiving_runtime"] = runtime
    reader = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        runtime.stock,
        permissions=("reports.view",),
    )
    await set_receiving_context(db_session, runtime.stock, reader)
    for table in ("unit_stock_lot", "unit_stock_entry"):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 1
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"),
        {"tenant": str(foreign_tenant.tenant_id)},
    )
    for table in ("unit_stock_lot", "unit_stock_entry"):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


async def test_received_source_helper_is_scoped_and_respects_acceptance_cutoff(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    incoming = await seed_received(db_session)
    source = (
        await db_session.execute(
            text(
                "SELECT i.created_unit_lot_id,d.accepted_at FROM incoming_item i "
                "JOIN incoming_document d ON d.id=i.document_id AND d.tenant_id=i.tenant_id "
                "WHERE i.id=:id"
            ),
            {"id": incoming.item_ids[0]},
        )
    ).one()
    lot_id, accepted_at = source
    assert isinstance(lot_id, UUID) and isinstance(accepted_at, datetime)
    await db_session.commit()
    reader = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        runtime.stock,
        permissions=("reports.view",),
    )
    await set_receiving_context(db_session, runtime.stock, reader)
    query = text(
        "SELECT public.unit_lot_is_received_for_report(" ":tenant,:branch,:catalog,:lot,:cutoff)"
    )
    parameters = {
        "tenant": runtime.stock.tenant_id,
        "branch": runtime.stock.branch_id,
        "catalog": runtime.stock.catalog_id,
        "lot": lot_id,
        "cutoff": accepted_at + timedelta(microseconds=1),
    }
    assert await db_session.scalar(query, parameters) is True
    assert await db_session.scalar(query, {**parameters, "cutoff": accepted_at}) is False
    for field in ("tenant", "branch", "catalog", "lot"):
        assert await db_session.scalar(query, {**parameters, field: uuid4()}) is False
    async with AsyncSession(db_engine) as support:
        with pytest.raises(DBAPIError):
            async with support.begin():
                await support.execute(query, parameters)
