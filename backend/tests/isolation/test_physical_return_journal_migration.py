"""Physical quarantine history rejects forged facts and cross-branch database access."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_return_journal_models import (
    PhysicalReturnCommand,
    PhysicalReturnLine,
    PhysicalReturnSlice,
)
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_return_journal_service import PhysicalReturnJournalService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_return_journal import TABLES, journal_facts, receive
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import cash_refund_migration

db_session = receiving_session_fixture


class ForgedJournalRepository(PhysicalReturnJournalRepository):
    def __init__(self, session: AsyncSession, corruption: str) -> None:
        super().__init__(session)
        self.corruption = corruption

    async def insert(
        self,
        command: PhysicalReturnCommand,
        lines: tuple[PhysicalReturnLine, ...],
        slices: tuple[PhysicalReturnSlice, ...],
    ) -> None:
        if self.corruption == "net":
            lines[0].net_amount += 1
        elif self.corruption == "cursor":
            lines[0].offset += 1
        elif self.corruption == "cost":
            slices[0].cost_amount += 1
        elif self.corruption == "source":
            slices[0].source_movement_id = uuid4()
        elif self.corruption == "range":
            slices[0].source_offset += 1
        elif self.corruption == "hash":
            command.request_hash = "0" * 64
        await super().insert(command, lines, () if self.corruption == "incomplete" else slices)


@pytest.mark.parametrize(
    "corruption", ["net", "cursor", "cost", "source", "range", "hash", "incomplete"]
)
async def test_database_rejects_forged_allocation_and_rolls_back_every_row(
    db_session: AsyncSession, corruption: str
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    operation_id = uuid4()
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await PhysicalReturnJournalService(
                ForgedJournalRepository(db_session, corruption)
            ).receive(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                operation_id=operation_id,
                parent_sale_id=source.sale.id,
                selections=(PhysicalReturnSelection(item.id, "unit", 1),),
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert all(not rows for rows in (await journal_facts(db_session)).values())
    result = await receive(db_session, source.sale.id, item.id, operation_id=operation_id)
    assert result.lines[0].offset == 0


async def test_journal_is_immutable_and_audit_masks_all_costs(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    result = await receive(db_session, source.sale.id, item.id)
    for table in TABLES:
        identifier = "id" if table == TABLES[0] else "command_id"
        for statement in (
            f"UPDATE {table} SET branch_id=branch_id WHERE {identifier}=:id",
            f"DELETE FROM {table} WHERE {identifier}=:id",
        ):
            with pytest.raises(DBAPIError):
                async with db_session.begin_nested():
                    await db_session.execute(text(statement), {"id": result.operation_id})
        costs = list(
            (
                await db_session.execute(
                    text(
                        "SELECT new_values->>:field FROM audit_log WHERE tenant_id=:tenant "
                        "AND table_name=:table AND action='INSERT'"
                    ),
                    {
                        "field": "total_cost_amount" if table == TABLES[0] else "cost_amount",
                        "tenant": result.tenant_id,
                        "table": table,
                    },
                )
            ).scalars()
        )
        assert costs and all(cost == "***" for cost in costs)
        unsafe_costs = await db_session.scalar(
            text(
                "SELECT count(*) FROM audit_log WHERE tenant_id=:tenant AND table_name=:table "
                "AND action='INSERT' AND (jsonb_path_exists(new_values, "
                "'$.**.cost_amount ? (@ != \"***\")') OR jsonb_path_exists(new_values, "
                "'$.**.total_cost_amount ? (@ != \"***\")'))"
            ),
            {"tenant": result.tenant_id, "table": table},
        )
        assert unsafe_costs == 0
    payload = await db_session.scalar(
        text(
            "SELECT new_values->>'result_payload' FROM audit_log WHERE tenant_id=:tenant "
            "AND table_name='pos_physical_return_command' AND record_id=:id AND action='INSERT'"
        ),
        {"tenant": result.tenant_id, "id": result.operation_id},
    )
    assert payload == "***"


async def test_refund_reader_cannot_read_or_insert_into_other_branch_or_tenant(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    result = await receive(db_session, source.sale.id, item.id)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    for table in TABLES:
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 1
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        branch_id = await support.scalar(
            text(
                "INSERT INTO branch(tenant_id,name) "
                "VALUES(:tenant,'Physical return isolated branch') RETURNING id"
            ),
            {"tenant": context.stock.tenant_id},
        )
    assert isinstance(branch_id, UUID)
    foreign = LedgerContext(context.stock.tenant_id, branch_id, context.stock.catalog_id)
    foreign_actor = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, foreign, foreign_actor)
    for table in TABLES:
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await receive(db_session, source.sale.id, item.id, actor_id=foreign_actor)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_physical_return_command "
                    "(tenant_id,id,parent_sale_id,parent_checkout_id,stock_command_id,branch_id,"
                    "actor_id,request_hash,state,total_amount,total_cost_amount,result_payload) "
                    "VALUES(:tenant,:id,:sale,:checkout,:stock,:branch,:actor,:hash,"
                    "'prepared_quarantine',1,1,'{}'::jsonb)"
                ),
                {
                    "tenant": context.stock.tenant_id,
                    "id": uuid4(),
                    "sale": source.sale.id,
                    "checkout": uuid4(),
                    "stock": uuid4(),
                    "branch": result.branch_id,
                    "actor": foreign_actor,
                    "hash": "0" * 64,
                },
            )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    for table in TABLES:
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0183_physical_return_journal.py"
    spec = importlib.util.spec_from_file_location("physical_return_journal_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_migration_roundtrip_preserves_rls_acl_or_refuses_committed_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_physical_return_command)")):
                with pytest.raises(DBAPIError):
                    with connection.begin_nested():
                        cash_refund_migration().downgrade()
                        migration().downgrade()
                return
            cash_refund_migration().downgrade()
            migration().downgrade()
            for table in TABLES:
                assert (
                    connection.scalar(text("SELECT to_regclass(:table)"), {"table": table}) is None
                )
            migration().upgrade()
            cash_refund_migration().upgrade()
            for table in TABLES:
                assert (
                    connection.scalar(
                        text(
                            "SELECT relrowsecurity AND relforcerowsecurity "
                            "FROM pg_class WHERE oid=to_regclass(:table)"
                        ),
                        {"table": table},
                    )
                    is True
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT has_table_privilege('aurum_app',:table,'INSERT') "
                            "AND NOT has_table_privilege('aurum_app',:table,'UPDATE') "
                            "AND NOT has_table_privilege('aurum_app',:table,'DELETE')"
                        ),
                        {"table": table},
                    )
                    is True
                )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
