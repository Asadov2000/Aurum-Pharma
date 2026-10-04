"""Quarantine return history is complete, immutable and protected by scoped RLS."""

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
from app.domains.pos.unit_return_models import UnitReturnCommand, UnitReturnSlice
from app.domains.pos.unit_return_repository import UnitReturnRepository
from app.domains.pos.unit_return_service import UnitReturnService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext
from tests.domains.pos.test_unit_return import (
    post_return_original,
    return_units,
    runtime,
    seed_return_source,
)

db_session = receiving_session_fixture


def _migration() -> ModuleType:
    source = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0169_prepared_unit_return_quarantine.py"
    )
    spec = importlib.util.spec_from_file_location("unit_return_quarantine_migration", source)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


class ForgedReturnRepository(UnitReturnRepository):
    def __init__(self, session: AsyncSession, corruption: str, foreign_entry_id: UUID) -> None:
        super().__init__(session)
        self.corruption = corruption
        self.foreign_entry_id = foreign_entry_id

    async def insert_command(self, command: UnitReturnCommand) -> None:
        if self.corruption == "net":
            command.net_amount += 1
        elif self.corruption == "cursor":
            command.allocation_offset += 1
        elif self.corruption == "hash":
            command.request_hash = "0" * 64
        await super().insert_command(command)

    async def insert_slices(self, slices: tuple[UnitReturnSlice, ...]) -> None:
        if self.corruption == "incomplete":
            return
        if self.corruption == "cost":
            slices[0].cost_amount += 1
        elif self.corruption == "range":
            slices[0].unit_offset += 1
        elif self.corruption == "entry":
            slices[0].original_entry_id = self.foreign_entry_id
        await super().insert_slices(slices)


@pytest.mark.parametrize(
    "corruption", ["net", "cursor", "hash", "cost", "range", "entry", "incomplete"]
)
async def test_database_rejects_forged_or_incomplete_returns_and_rolls_back_claim(
    db_session: AsyncSession,
    corruption: str,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=3)
    other = await post_return_original(db_session, quantity=3)
    operation_id = uuid4()
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await UnitReturnService(
                ForgedReturnRepository(
                    db_session,
                    corruption,
                    other.cost_slices[0].entry_id,
                )
            ).return_received_line(
                tenant_id=context.stock.tenant_id,
                branch_id=context.stock.branch_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                operation_id=operation_id,
                original_line_id=original.snapshot.id,
                quantity=1,
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    for table in ("pos_unit_return_command", "pos_unit_return_slice"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == 0
        )
    returned = await return_units(db_session, original.snapshot.id, 1, operation_id=operation_id)
    assert returned.allocation_offset == 0


async def test_return_commands_and_physical_quarantine_slices_cannot_be_changed_or_deleted(
    db_session: AsyncSession,
) -> None:
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=3)
    returned = await return_units(db_session, original.snapshot.id, 1)
    for table, identifier in (
        ("pos_unit_return_command", returned.command_id),
        ("pos_unit_return_slice", returned.slices[0].id),
    ):
        for statement in (
            f"UPDATE {table} SET cost_amount=cost_amount+1 WHERE id=:id",
            f"DELETE FROM {table} WHERE id=:id",
        ):
            with pytest.raises(DBAPIError):
                async with db_session.begin_nested():
                    await db_session.execute(text(statement), {"id": identifier})


async def test_return_only_actor_sees_own_quarantine_and_cannot_forge_tenant_context(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=3)
    returned = await return_units(db_session, original.snapshot.id, 1)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=("pos.refund",),
    )
    await set_receiving_context(db_session, context.stock, actor)
    for table in ("pos_unit_return_command", "pos_unit_return_slice"):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 1
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        foreign_branch = await support.scalar(
            text(
                "INSERT INTO branch(tenant_id,name) "
                "VALUES(:tenant,'Return foreign branch') RETURNING id"
            ),
            {"tenant": context.stock.tenant_id},
        )
    assert isinstance(foreign_branch, UUID)
    foreign_stock = LedgerContext(
        context.stock.tenant_id,
        foreign_branch,
        context.stock.catalog_id,
    )
    foreign_actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        foreign_stock,
        permissions=("pos.refund",),
    )
    await set_receiving_context(db_session, foreign_stock, foreign_actor)
    for table in ("pos_unit_return_command", "pos_unit_return_slice"):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"),
        {"tenant": str(uuid4())},
    )
    for table in ("pos_unit_return_command", "pos_unit_return_slice"):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    assert isinstance(returned.command_id, UUID)


async def test_return_audit_snapshots_redact_procurement_cost_fields(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=3)
    returned = await return_units(db_session, original.snapshot.id, 1)
    command_cost = await db_session.scalar(
        text(
            "SELECT new_values->>'cost_amount' FROM audit_log "
            "WHERE tenant_id=:tenant AND table_name='pos_unit_return_command' "
            "AND record_id=:id AND action='INSERT'"
        ),
        {"tenant": context.stock.tenant_id, "id": returned.command_id},
    )
    assert command_cost == "***"
    for part in returned.slices:
        audit = (
            await db_session.execute(
                text(
                    "SELECT new_values->>'cost_amount',new_values->>'original_pack_cost' "
                    "FROM audit_log WHERE tenant_id=:tenant AND table_name='pos_unit_return_slice' "
                    "AND record_id=:id AND action='INSERT'"
                ),
                {"tenant": context.stock.tenant_id, "id": part.id},
            )
        ).one()
        assert tuple(audit) == ("***", "***")


async def test_quarantine_migration_empty_roundtrip_or_refuses_existing_history(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_unit_return_command)")):
                with pytest.raises(DBAPIError, match="unit_return_nonempty_downgrade_forbidden"):
                    with connection.begin_nested():
                        migration.downgrade()
                return
            migration.downgrade()
            for table in ("pos_unit_return_command", "pos_unit_return_slice"):
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:table)"),
                        {"table": table},
                    )
                    is None
                )
            migration.upgrade()
            for table in ("pos_unit_return_command", "pos_unit_return_slice"):
                assert (
                    connection.scalar(
                        text(
                            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                            "WHERE oid=to_regclass(:table)"
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


async def test_committed_return_blocks_downgrade_without_removing_quarantine(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    await seed_return_source(db_session)
    original = await post_return_original(db_session, quantity=1)
    returned = await return_units(db_session, original.snapshot.id, 1)
    await db_session.commit()
    migration = _migration()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError, match="unit_return_nonempty_downgrade_forbidden"):
                await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_return_command WHERE id=:id"),
            {"id": returned.command_id},
        )
        == 1
    )
    assert await db_session.scalar(
        text("SELECT count(*) FROM pos_unit_return_slice WHERE command_id=:id"),
        {"id": returned.command_id},
    ) == len(returned.slices)
