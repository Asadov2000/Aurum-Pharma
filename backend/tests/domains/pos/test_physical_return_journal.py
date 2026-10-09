"""Received physical returns preserve the completed sale and all sellable stock."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_physical_blocking import PhysicalLotBlockingService
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_return_journal_schemas import PhysicalReturnJournalResult
from app.domains.pos.physical_return_journal_service import PhysicalReturnJournalService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_physical_checkout_preparation import facts as checkout_facts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
TABLES = ("pos_physical_return_command", "pos_physical_return_line", "pos_physical_return_slice")


async def receive(
    session: AsyncSession,
    sale_id: UUID,
    line_id: UUID,
    quantity: int = 1,
    *,
    sale_format: SaleFormatV2 = "unit",
    operation_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> PhysicalReturnJournalResult:
    context = runtime(session)
    return await PhysicalReturnJournalService(PhysicalReturnJournalRepository(session)).receive(
        tenant_id=context.stock.tenant_id,
        actor_id=actor_id or context.actor_id,
        operation_id=operation_id or uuid4(),
        parent_sale_id=sale_id,
        selections=(PhysicalReturnSelection(line_id, sale_format, quantity),),
    )


async def journal_facts(session: AsyncSession) -> dict[str, list[object]]:
    return {
        table: list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": runtime(session).stock.tenant_id},
                )
            ).scalars()
        )
        for table in TABLES
    }


async def sale_stock_facts(session: AsyncSession) -> dict[str, list[object]]:
    facts = await checkout_facts(session)
    del facts["audit_log"]
    return facts


@pytest.mark.parametrize(
    "sale_format,quantity,units", [("unit", 2, 2), ("blister", 1, 10), ("pack", 1, 30)]
)
async def test_receiving_all_formats_only_posts_quarantine_journal(
    db_session: AsyncSession, sale_format: SaleFormatV2, quantity: int, units: int
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack", quantity=2)
    original = await complete(db_session, await saved_request(db_session, source.sale))
    before = await sale_stock_facts(db_session)
    result = await receive(db_session, source.sale.id, item.id, quantity, sale_format=sale_format)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result.parent_sale_id == original.sale_id
    assert result.actor_id == runtime(db_session).actor_id
    assert result.branch_id == original.branch_id
    assert result.state == "prepared_quarantine"
    assert result.lines[0].base_units == units
    assert result.lines[0].offset == 0
    assert sum(part.base_units for part in result.lines[0].slices) == units
    assert (
        sum((part.cost_amount for part in result.lines[0].slices), Decimal())
        == result.total_cost_amount
    )
    assert await sale_stock_facts(db_session) == before


async def test_split_returns_telescope_net_discount_cost_and_source_offsets(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(
        db_session, source.sale.id, sale_format="pack", quantity=2, discount=Decimal("0.01")
    )
    original = await complete(db_session, await saved_request(db_session, source.sale))
    parts = [
        await receive(db_session, source.sale.id, item.id, amount) for amount in (1, 10, 19, 30)
    ]
    assert [part.lines[0].offset for part in parts] == [0, 1, 11, 30]
    assert sum((part.total_amount for part in parts), Decimal()) == original.total_amount
    assert sum((part.lines[0].discount_amount for part in parts), Decimal()) == Decimal("0.01")
    assert (
        sum((part.lines[0].gross_amount for part in parts), Decimal())
        == original.lines[0].gross_amount
    )
    assert sum((part.total_cost_amount for part in parts), Decimal()) == Decimal("20.00")
    source_cost = await db_session.scalar(
        text(
            "SELECT sum(cost_amount) FROM unit_stock_movement_v2 "
            "WHERE command_id=:command AND units_taken>0"
        ),
        {"command": original.stock_command_id},
    )
    assert sum((part.total_cost_amount for part in parts), Decimal()) == source_cost
    before = await journal_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await receive(db_session, source.sale.id, item.id)
    assert await journal_facts(db_session) == before


async def test_replay_is_exact_and_changed_selection_conflicts(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    operation_id = uuid4()
    result = await receive(db_session, source.sale.id, item.id, operation_id=operation_id)
    before = await journal_facts(db_session)
    assert await receive(db_session, source.sale.id, item.id, operation_id=operation_id) == result
    with pytest.raises(ConflictError):
        await receive(db_session, source.sale.id, item.id, 2, operation_id=operation_id)
    assert await journal_facts(db_session) == before


async def test_one_overreturned_line_rejects_entire_multi_line_command(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    first = await put_physical(
        db_session, source.sale.id, position=1, sale_format="unit", quantity=2
    )
    second = await put_physical(db_session, source.sale.id, position=2, sale_format="blister")
    await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    before = await journal_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await PhysicalReturnJournalService(PhysicalReturnJournalRepository(db_session)).receive(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            operation_id=uuid4(),
            parent_sale_id=source.sale.id,
            selections=(
                PhysicalReturnSelection(first.id, "unit", 1),
                PhysicalReturnSelection(second.id, "unit", 11),
            ),
        )
    assert await journal_facts(db_session) == before
    result = await receive(db_session, source.sale.id, first.id)
    assert result.lines[0].offset == 0


async def test_blocked_source_is_still_returnable_without_releasing_stock(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    await PhysicalLotBlockingService(UnitDispensingV2Repository(db_session)).set_blocked(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        lot_id=source.stock.lot_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        is_blocked=True,
    )
    before = await sale_stock_facts(db_session)
    result = await receive(db_session, source.sale.id, item.id, sale_format="pack")
    assert result.lines[0].base_units == 30
    assert await sale_stock_facts(db_session) == before


async def test_expired_source_stays_returnable_without_becoming_sellable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    expiry = await db_session.scalar(
        text("SELECT expires_at FROM unit_stock_lot_v2 WHERE id=:id"),
        {"id": source.stock.lot_id},
    )
    assert isinstance(expiry, date)
    later = datetime.combine(expiry + timedelta(days=2), time(12), tzinfo=UTC)
    # Advance stock eligibility only; immutable received provenance stays intact.
    monkeypatch.setattr("app.domains.inventory.unit_dispensing_v2_service.utc_now", lambda: later)
    before = await sale_stock_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await dispense(db_session, source.stock, (cart_line(source.stock, "unit", 1),))
    result = await receive(db_session, source.sale.id, item.id, sale_format="pack")
    assert result.lines[0].base_units == 30
    assert await sale_stock_facts(db_session) == before


async def test_refund_only_actual_actor_can_receive_and_replay(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    result = await receive(db_session, source.sale.id, item.id, actor_id=actor)
    assert result.actor_id == actor
    assert (
        await receive(
            db_session, source.sale.id, item.id, actor_id=actor, operation_id=result.operation_id
        )
        == result
    )


@pytest.mark.parametrize("replay", [False, True])
async def test_wrong_actual_actor_tenant_and_missing_refund_are_denied(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine, replay: bool
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    operation_id = uuid4()
    if replay:
        await receive(db_session, source.sale.id, item.id, operation_id=operation_id)
    context = runtime(db_session)
    before = await journal_facts(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.sell",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    for claimed_actor in (actor, context.actor_id):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await receive(
                db_session,
                source.sale.id,
                item.id,
                actor_id=claimed_actor,
                operation_id=operation_id,
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await PhysicalReturnJournalService(PhysicalReturnJournalRepository(db_session)).receive(
            tenant_id=uuid4(),
            actor_id=context.actor_id,
            operation_id=operation_id,
            parent_sale_id=source.sale.id,
            selections=(PhysicalReturnSelection(item.id, "unit", 1),),
        )
    assert await journal_facts(db_session) == before
    if not replay:
        assert all(not rows for rows in before.values())


async def test_outer_rollback_removes_claim_lines_and_slices(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id = source.sale.id, item.id
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    operation_id = uuid4()
    await receive(db_session, sale_id, line_id, 2, operation_id=operation_id)
    await db_session.rollback()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert all(not rows for rows in (await journal_facts(db_session)).values())
    retry = await receive(db_session, sale_id, line_id, 2, operation_id=operation_id)
    assert retry.lines[0].offset == 0


async def test_revoked_assignment_blocks_replay_and_new_operation(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    result = await receive(db_session, source.sale.id, item.id)
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET is_active=false "
                "WHERE user_id=:actor AND tenant_id=:tenant"
            ),
            {"actor": context.actor_id, "tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    for operation_id in (result.operation_id, uuid4()):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await receive(db_session, source.sale.id, item.id, operation_id=operation_id)


@pytest.mark.parametrize("status", ["blocked", "invited"])
async def test_inactive_account_blocks_replay_and_new_operation(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, status: str
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    result = await receive(db_session, source.sale.id, item.id)
    context = runtime(db_session)
    before = await journal_facts(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE app_user SET status=:status WHERE id=:id"),
            {"status": status, "id": context.actor_id},
        )
    try:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        for operation_id in (result.operation_id, uuid4()):
            with pytest.raises((PermissionDeniedError, NotFoundError)):
                await receive(db_session, source.sale.id, item.id, operation_id=operation_id)
        await db_session.rollback()
    finally:
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE app_user SET status='active' WHERE id=:id"),
                {"id": context.actor_id},
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await journal_facts(db_session) == before


async def test_blocked_pos_module_rejects_insert_without_journal_rows(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    before = await journal_facts(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
        await owner.execute(
            text("ALTER TABLE tenant_access_policy DISABLE TRIGGER a00_guard_tenant_access_policy")
        )
        await owner.execute(
            text(
                "UPDATE tenant_access_policy SET version=version+1,blocked_modules=:modules "
                "WHERE tenant_id=:tenant"
            ),
            {"modules": ["pos"], "tenant": context.stock.tenant_id},
        )
        await owner.execute(
            text("ALTER TABLE tenant_access_policy ENABLE TRIGGER a00_guard_tenant_access_policy")
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    with pytest.raises(DBAPIError, match="tenant_access_restricted"):
        async with db_session.begin_nested():
            await receive(db_session, source.sale.id, item.id)
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await journal_facts(db_session) == before
    for table in TABLES:
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


async def concurrent_receive(
    factory: async_sessionmaker[AsyncSession],
    context: ReceivingRuntime,
    sale_id: UUID,
    line_id: UUID,
    operation_id: UUID,
) -> PhysicalReturnJournalResult | BusinessRuleError:
    try:
        async with factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            return await receive(session, sale_id, line_id, operation_id=operation_id)
    except BusinessRuleError as error:
        return error


@pytest.mark.parametrize("same_operation", [False, True])
async def test_concurrent_commands_do_not_allocate_the_same_last_unit_twice(
    db_session: AsyncSession, same_operation: bool
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="unit")
    await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    await db_session.commit()
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                concurrent_receive(
                    context.factory,
                    context,
                    source.sale.id,
                    item.id,
                    operation_id if same_operation else uuid4(),
                )
                for _ in range(3)
            )
        ),
        timeout=15,
    )
    winners = [result for result in results if isinstance(result, PhysicalReturnJournalResult)]
    assert len(winners) == (3 if same_operation else 1)
    assert all(result == winners[0] for result in winners)
    assert sum(isinstance(result, BusinessRuleError) for result in results) == (
        0 if same_operation else 2
    )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert len((await journal_facts(db_session))[TABLES[0]]) == 1
