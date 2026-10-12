"""Mixed quarantine history rejects forged facts, overlap and cross-scope access."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
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
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection, MixedReturnSelection
from app.domains.pos.mixed_return_journal_models import (
    MixedReturnCommand,
    MixedReturnLine,
    MixedReturnSlice,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_schemas import MixedReturnJournalResult
from app.domains.pos.mixed_return_journal_service import MixedReturnJournalService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext
from tests.domains.pos.test_mixed_cash_checkout import complete
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    mixed_cash_refund_migration,
    mixed_zero_return_migration,
)

db_session = receiving_session_fixture
TABLES = ("pos_mixed_return_command", "pos_mixed_return_line", "pos_mixed_return_slice")


async def _source(session: AsyncSession) -> MixedCheckoutResultV4:
    source = await checkout_source(session)
    await legacy_with_stock(session, source.sale)
    await put_physical(session, source.sale.id, sale_format="pack")
    return await complete(session, await saved_request(session, source.sale))


def _selections(source: MixedCheckoutResultV4) -> tuple[MixedReturnSelection, ...]:
    return (
        LegacyReturnSelection(source.lines[0].id, Decimal("0.001")),
        PhysicalReturnSelection(source.lines[1].id, "unit", 1),
    )


async def _receive(
    session: AsyncSession,
    source: MixedCheckoutResultV4,
    *,
    actor_id: UUID | None = None,
    operation_id: UUID | None = None,
    selections: tuple[MixedReturnSelection, ...] | None = None,
) -> MixedReturnJournalResult:
    return await MixedReturnJournalService(MixedReturnJournalRepository(session)).receive(
        tenant_id=source.tenant_id,
        actor_id=actor_id or runtime(session).actor_id,
        operation_id=operation_id or uuid4(),
        parent_sale_id=source.sale_id,
        selections=_selections(source) if selections is None else selections,
    )


async def _journal_counts(session: AsyncSession) -> tuple[int, ...]:
    counts: list[int] = []
    for table in TABLES:
        count = await session.scalar(text(f"SELECT count(*) FROM {table}"))
        counts.append(int(count or 0))
    return tuple(counts)


class ForgedJournalRepository(MixedReturnJournalRepository):
    def __init__(self, session: AsyncSession, corruption: str) -> None:
        super().__init__(session)
        self.corruption = corruption

    # Keep each deliberately forged fact visible instead of hiding it in callbacks.
    async def insert(  # noqa: PLR0912
        self,
        command: MixedReturnCommand,
        lines: tuple[MixedReturnLine, ...],
        slices: tuple[MixedReturnSlice, ...],
    ) -> None:
        legacy = next(line for line in lines if line.inventory_kind == "legacy_pack")
        physical = next(line for line in lines if line.inventory_kind == "physical_unit")
        if self.corruption == "net":
            legacy.net_amount += 1
        elif self.corruption == "legacy_cursor":
            legacy.legacy_offset = Decimal("0.001")
        elif self.corruption == "physical_cursor":
            physical.offset = 1
        elif self.corruption == "cost":
            slices[0].cost_amount += 1
        elif self.corruption == "source":
            slices[0].source_movement_id = uuid4()
        elif self.corruption == "range":
            slices[0].source_offset += 1
        elif self.corruption == "hash":
            command.request_hash = "0" * 64
        elif self.corruption == "payload":
            command.result_payload = {}
        elif self.corruption == "position":
            physical.position += 1
        elif self.corruption == "batch":
            legacy.batch_id = uuid4()
        elif self.corruption == "legacy_shape":
            legacy.base_units = 1
        elif self.corruption == "physical_shape":
            physical.legacy_quantity = Decimal("0.001")
        elif self.corruption == "legacy_cost":
            legacy.cost_amount = Decimal("0.01")
        elif self.corruption == "slice_position":
            slices[0].position += 1
        await super().insert(command, lines, () if self.corruption == "incomplete" else slices)


@pytest.mark.parametrize(
    "corruption",
    [
        "net",
        "legacy_cursor",
        "physical_cursor",
        "cost",
        "source",
        "range",
        "hash",
        "payload",
        "position",
        "batch",
        "legacy_shape",
        "physical_shape",
        "legacy_cost",
        "slice_position",
        "incomplete",
    ],
)
async def test_database_rejects_forged_mixed_history_and_rolls_back_every_row(
    db_session: AsyncSession, corruption: str
) -> None:
    source = await _source(db_session)
    operation_id = uuid4()
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await MixedReturnJournalService(
                ForgedJournalRepository(db_session, corruption)
            ).receive(
                tenant_id=source.tenant_id,
                actor_id=runtime(db_session).actor_id,
                operation_id=operation_id,
                parent_sale_id=source.sale_id,
                selections=_selections(source),
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await _journal_counts(db_session) == (0, 0, 0)
    result = await _receive(db_session, source, operation_id=operation_id)
    assert result.lines[0].offset == Decimal("0.000")
    assert result.lines[1].offset == 0


@pytest.mark.parametrize("inventory_kind", ["legacy_pack", "physical_unit"])
async def test_direct_insert_cannot_overlap_an_existing_inventory_cursor(
    db_session: AsyncSession, inventory_kind: str
) -> None:
    source = await _source(db_session)
    first = await _receive(db_session, source)
    operation_id = uuid4()
    with pytest.raises(DBAPIError, match="mixed_return_allocation_invalid"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_mixed_return_command "
                    "SELECT tenant_id,:id,parent_sale_id,parent_checkout_id,stock_command_id,"
                    "branch_id,actor_id,request_hash,state,total_amount,physical_cost_amount,"
                    "result_payload,created_at,updated_at,created_by,updated_by "
                    "FROM pos_mixed_return_command WHERE id=:first"
                ),
                {"id": operation_id, "first": first.operation_id},
            )
            await db_session.execute(
                text(
                    "INSERT INTO pos_mixed_return_line "
                    "SELECT tenant_id,:id,line_id,position,branch_id,catalog_id,inventory_kind,"
                    'batch_id,legacy_quantity,legacy_offset,base_units,"offset",sale_format,'
                    "quantity,cost_amount,net_amount,discount_amount,gross_amount,created_at,"
                    "updated_at,created_by,updated_by FROM pos_mixed_return_line "
                    "WHERE command_id=:first AND inventory_kind=:kind"
                ),
                {"id": operation_id, "first": first.operation_id, "kind": inventory_kind},
            )
    assert await _journal_counts(db_session) == (1, 2, 1)
    second = await _receive(
        db_session,
        source,
        operation_id=operation_id,
        selections=(PhysicalReturnSelection(source.lines[1].id, "unit", 1),),
    )
    assert second.lines[0].offset == 1
    # A partial return keeps original global position 2; subsets are not dense.
    assert second.lines[0].position == 2


async def test_mixed_journal_is_immutable_and_audit_hides_payload_and_costs(
    db_session: AsyncSession,
) -> None:
    source = await _source(db_session)
    result = await _receive(db_session, source)
    for table in TABLES:
        identifier = "id" if table == TABLES[0] else "command_id"
        for statement in (
            f"UPDATE {table} SET branch_id=branch_id WHERE {identifier}=:id",
            f"DELETE FROM {table} WHERE {identifier}=:id",
        ):
            with pytest.raises(DBAPIError):
                async with db_session.begin_nested():
                    await db_session.execute(text(statement), {"id": result.operation_id})
        field = "physical_cost_amount" if table == TABLES[0] else "cost_amount"
        values = list(
            (
                await db_session.execute(
                    text(
                        "SELECT new_values->>:field FROM audit_log WHERE tenant_id=:tenant "
                        "AND table_name=:table AND action='INSERT'"
                    ),
                    {"field": field, "tenant": result.tenant_id, "table": table},
                )
            ).scalars()
        )
        assert "***" in values and all(value in (None, "***") for value in values)
    assert (
        await db_session.scalar(
            text(
                "SELECT new_values->>'result_payload' FROM audit_log WHERE tenant_id=:tenant "
                "AND table_name='pos_mixed_return_command' AND record_id=:id AND action='INSERT'"
            ),
            {"tenant": result.tenant_id, "id": result.operation_id},
        )
        == "***"
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT cost_amount IS NULL FROM pos_mixed_return_line WHERE command_id=:id "
                "AND inventory_kind='legacy_pack'"
            ),
            {"id": result.operation_id},
        )
        is True
    )


async def test_refund_only_actor_cannot_access_other_refund_scope(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await _source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    result = await _receive(db_session, source, actor_id=actor)
    assert await _journal_counts(db_session) == (1, 2, 1)
    # Journal writes retain a shared tenant-access lock. Branch creation needs
    # its exclusive counterpart on another connection; finish this write first.
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        branch_id = await support.scalar(
            text(
                "INSERT INTO branch(tenant_id,name) "
                "VALUES(:tenant,'Mixed return isolated branch') RETURNING id"
            ),
            {"tenant": context.stock.tenant_id},
        )
    assert isinstance(branch_id, UUID)
    foreign = LedgerContext(context.stock.tenant_id, branch_id, context.stock.catalog_id)
    foreign_actor = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, foreign, foreign_actor)
    assert await _journal_counts(db_session) == (0, 0, 0)
    for table in ("pos_mixed_checkout", "unit_stock_cart_line_v2"):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    legacy_identity = {"tenant": source.tenant_id, "id": source.lines[0].id}
    # Legacy sale_item has always used tenant-wide SELECT. This journal does not
    # replace that historical policy; its refund source must have a visible,
    # authorized mixed marker before legacy rows can be used.
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM sale_item WHERE tenant_id=:tenant AND id=:id"),
            legacy_identity,
        )
        == 1
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM sale_item item JOIN pos_mixed_checkout checkout "
                "ON checkout.tenant_id=item.tenant_id AND checkout.sale_id=item.sale_id "
                "WHERE item.tenant_id=:tenant AND item.id=:id"
            ),
            legacy_identity,
        )
        == 0
    )
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await _receive(db_session, source, actor_id=foreign_actor)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_mixed_return_command "
                    "(tenant_id,id,parent_sale_id,parent_checkout_id,stock_command_id,branch_id,"
                    "actor_id,request_hash,state,total_amount,physical_cost_amount,result_payload) "
                    "VALUES(:tenant,:id,:sale,:checkout,:stock,:branch,:actor,:hash,"
                    "'prepared_quarantine',1,1,'{}'::jsonb)"
                ),
                {
                    "tenant": context.stock.tenant_id,
                    "id": uuid4(),
                    "sale": source.sale_id,
                    "checkout": source.operation_id,
                    "stock": source.stock_command_id,
                    "branch": result.branch_id,
                    "actor": foreign_actor,
                    "hash": "0" * 64,
                },
            )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert await _journal_counts(db_session) == (0, 0, 0)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM sale_item WHERE tenant_id=:tenant AND id=:id"),
            legacy_identity,
        )
        == 0
    )


async def test_support_session_cannot_insert_mixed_quarantine_history(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await _source(db_session)
    result = await _receive(db_session, source)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        with pytest.raises(DBAPIError):
            async with support.begin_nested():
                await support.execute(
                    text(
                        "INSERT INTO pos_mixed_return_command "
                        "SELECT tenant_id,:id,parent_sale_id,parent_checkout_id,stock_command_id,"
                        "branch_id,actor_id,request_hash,state,total_amount,physical_cost_amount,"
                        "result_payload,created_at,updated_at,created_by,updated_by "
                        "FROM pos_mixed_return_command WHERE id=:first"
                    ),
                    {"id": uuid4(), "first": result.operation_id},
                )


async def test_repeatable_read_cannot_use_a_stale_return_cursor(
    db_session: AsyncSession,
) -> None:
    source = await _source(db_session)
    result = await _receive(db_session, source)
    await db_session.commit()
    context = runtime(db_session)
    await db_session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    await set_receiving_context(db_session, context.stock, context.actor_id)
    with pytest.raises(DBAPIError, match="mixed_return_isolation_invalid"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_mixed_return_command "
                    "SELECT tenant_id,:id,parent_sale_id,parent_checkout_id,stock_command_id,"
                    "branch_id,actor_id,request_hash,state,total_amount,physical_cost_amount,"
                    "result_payload,created_at,updated_at,created_by,updated_by "
                    "FROM pos_mixed_return_command WHERE id=:first"
                ),
                {"id": uuid4(), "first": result.operation_id},
            )


async def test_direct_command_without_lines_cannot_commit(db_session: AsyncSession) -> None:
    source = await _source(db_session)
    result = await _receive(db_session, source)
    with pytest.raises(DBAPIError, match="mixed_return_document_invalid"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_mixed_return_command "
                    "SELECT tenant_id,:id,parent_sale_id,parent_checkout_id,stock_command_id,"
                    "branch_id,actor_id,request_hash,state,total_amount,physical_cost_amount,"
                    "result_payload,created_at,updated_at,created_by,updated_by "
                    "FROM pos_mixed_return_command WHERE id=:first"
                ),
                {"id": uuid4(), "first": result.operation_id},
            )
            await db_session.execute(text("SET CONSTRAINTS mixed_return_complete IMMEDIATE"))
    assert await _journal_counts(db_session) == (1, 2, 1)


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0189_mixed_return_journal.py"
    spec = importlib.util.spec_from_file_location("mixed_return_journal_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_migration_roundtrip_preserves_guards_acl_or_refuses_committed_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            guards = text(
                "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef FROM pg_proc "
                "WHERE pronamespace='public'::regnamespace AND proname IN "
                "('trg_mixed_return_guard','trg_mixed_return_complete',"
                "'trg_mixed_checkout_return_boundary','trg_guard_sale_immutability',"
                "'audit_redact_table_snapshot') ORDER BY proname"
            )
            original_guards = connection.execute(guards).all()
            module = migration()
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_mixed_return_command)")):
                with pytest.raises(DBAPIError, match="mixed_return_history_exists") as error:
                    with connection.begin_nested():
                        module.downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                assert connection.execute(guards).all() == original_guards
                return
            mixed_zero_return_migration().downgrade()
            mixed_cash_refund_migration().downgrade()
            module.downgrade()
            for table in TABLES:
                assert (
                    connection.scalar(text("SELECT to_regclass(:table)"), {"table": table}) is None
                )
            module.upgrade()
            mixed_cash_refund_migration().upgrade()
            mixed_zero_return_migration().upgrade()
            assert connection.execute(guards).all() == original_guards
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
                            "AND NOT has_table_privilege('aurum_app',:table,'DELETE') "
                            "AND NOT has_table_privilege('aurum_support',:table,'INSERT')"
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
