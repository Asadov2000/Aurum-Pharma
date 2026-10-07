"""Real app permissions and SQL posting both enforce physical lot blocking."""

import asyncio
from dataclasses import replace
from datetime import date
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_allocation_v2 import plan_unit_cart_stock_v2
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_ledger_v2_models import UnitStockMovementV2
from app.domains.inventory.unit_physical_blocking import (
    PhysicalLotBlockingService,
    PhysicalLotBlockState,
)
from app.domains.inventory.unit_stock_v2 import stock_availability_v2
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import LedgerContext
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    PhysicalSource,
    cart_line,
    dispense,
    receive_source,
    remaining,
)

db_session = receiving_session_fixture


async def set_blocked(
    session: AsyncSession, source: PhysicalSource, blocked: bool
) -> PhysicalLotBlockState:
    context = source.incoming
    return await PhysicalLotBlockingService(UnitDispensingV2Repository(session)).set_blocked(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        lot_id=source.lot_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        is_blocked=blocked,
    )


async def state(session: AsyncSession, source: PhysicalSource) -> tuple[bool, int]:
    row = (
        await session.execute(
            text("SELECT is_blocked,blocking_revision FROM unit_stock_lot_v2 WHERE id=:id"),
            {"id": source.lot_id},
        )
    ).one()
    return row.is_blocked, row.blocking_revision


async def facts(session: AsyncSession, source: PhysicalSource) -> dict[str, list[object]]:
    return {
        table: list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": source.incoming.stock.tenant_id},
                )
            ).scalars()
        )
        for table in (
            "unit_stock_lot_v2",
            "unit_stock_opened_pack_v2",
            "unit_stock_movement_v2",
            "unit_stock_cart_command_v2",
            "unit_stock_cart_line_v2",
            "audit_log",
        )
    }


async def test_block_excludes_sealed_and_opened_stock_and_unblock_restores_it(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 3),))
    assert await remaining(db_session, source) == 57
    assert await state(db_session, source) == (False, 0)
    await set_blocked(db_session, source, True)
    assert await state(db_session, source) == (True, 1)
    stock = source.incoming.stock
    snapshots = await UnitDispensingV2Repository(db_session).lock_stock_snapshots(
        stock.tenant_id, stock.branch_id, [stock.catalog_id]
    )
    availability = stock_availability_v2(
        batches=snapshots,
        tenant_id=stock.tenant_id,
        branch_id=stock.branch_id,
        catalog_id=stock.catalog_id,
        today=date.today(),
        packaging=source.packaging,
    )
    assert (availability.base_units, availability.closed_packs, availability.intact_blisters) == (
        0,
        0,
        0,
    )
    with pytest.raises(ValueError):
        plan_unit_cart_stock_v2(
            lines=(cart_line(source, "unit", 1),),
            batches=snapshots,
            tenant_id=stock.tenant_id,
            branch_id=stock.branch_id,
            today=date.today(),
        )
    before = await facts(db_session, source)
    with pytest.raises(BusinessRuleError):
        await dispense(db_session, source, (cart_line(source, "unit", 1),))
    assert await facts(db_session, source) == before
    await set_blocked(db_session, source, False)
    assert await state(db_session, source) == (False, 2)
    await dispense(db_session, source, (cart_line(source, "blister", 1),))
    assert await remaining(db_session, source) == 47


async def test_noop_does_not_touch_stock_revision_or_audit(db_session: AsyncSession) -> None:
    source = await receive_source(db_session)
    before = await facts(db_session, source)
    await set_blocked(db_session, source, False)
    assert await facts(db_session, source) == before
    await set_blocked(db_session, source, True)
    before = await facts(db_session, source)
    await set_blocked(db_session, source, True)
    assert await facts(db_session, source) == before


async def test_support_context_cannot_use_ordinary_blocking_service(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    async with AsyncSession(db_engine) as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "SELECT set_config('app.tenant_id',:tenant,true),"
                "set_config('app.user_id',:actor,true)"
            ),
            {
                "tenant": str(source.incoming.stock.tenant_id),
                "actor": str(source.incoming.actor_id),
            },
        )
        assert await support.scalar(text("SELECT current_user")) == "aurum_support"
        with pytest.raises(PermissionDeniedError):
            await set_blocked(support, source, True)
    assert await state(db_session, source) == (False, 0)


async def test_only_batch_permissions_are_needed_to_change_block(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    stock = source.incoming.stock
    # Receiving holds access-revision locks; release them before another connection
    # publishes a new test role and assignment.
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        stock,
        permissions=("batches.view", "batches.update"),
    )
    await set_receiving_context(db_session, stock, actor)
    result = await PhysicalLotBlockingService(UnitDispensingV2Repository(db_session)).set_blocked(
        tenant_id=stock.tenant_id,
        branch_id=stock.branch_id,
        lot_id=source.lot_id,
        actor_id=actor,
        allowed_branch_ids={stock.branch_id},
        is_blocked=True,
    )
    assert result.lot_id == source.lot_id
    assert result.branch_id == stock.branch_id
    assert result.is_blocked is True
    assert result.blocking_revision == 1


async def test_pos_permission_cannot_change_block_through_service_or_raw_sql(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    stock = source.incoming.stock
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, stock, permissions=("pos.sell",)
    )
    await set_receiving_context(db_session, stock, actor)
    with pytest.raises(PermissionDeniedError):
        await PhysicalLotBlockingService(UnitDispensingV2Repository(db_session)).set_blocked(
            tenant_id=stock.tenant_id,
            branch_id=stock.branch_id,
            lot_id=source.lot_id,
            actor_id=actor,
            allowed_branch_ids={stock.branch_id},
            is_blocked=True,
        )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE unit_stock_lot_v2 SET is_blocked=true WHERE id=:id"),
                {"id": source.lot_id},
            )
    assert await state(db_session, source) == (False, 0)


@pytest.mark.parametrize("invalid", ["actor", "tenant", "branch", "allowed_branch"])
async def test_forged_scope_is_rejected_before_modification(
    db_session: AsyncSession,
    invalid: str,
) -> None:
    source = await receive_source(db_session)
    stock = source.incoming.stock
    values = {
        "actor_id": source.incoming.actor_id,
        "tenant_id": stock.tenant_id,
        "branch_id": stock.branch_id,
        "allowed_branch_ids": {stock.branch_id},
    }
    values[
        {
            "actor": "actor_id",
            "tenant": "tenant_id",
            "branch": "branch_id",
            "allowed_branch": "allowed_branch_ids",
        }[invalid]
    ] = (
        set() if invalid == "allowed_branch" else uuid4()
    )
    with pytest.raises(PermissionDeniedError):
        await PhysicalLotBlockingService(UnitDispensingV2Repository(db_session)).set_blocked(
            tenant_id=cast(UUID, values["tenant_id"]),
            branch_id=cast(UUID, values["branch_id"]),
            actor_id=cast(UUID, values["actor_id"]),
            allowed_branch_ids=cast(set[UUID], values["allowed_branch_ids"]),
            lot_id=source.lot_id,
            is_blocked=True,
        )
    assert await state(db_session, source) == (False, 0)


async def test_missing_lot_and_non_boolean_flag_do_not_change_stock(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    stock = source.incoming.stock
    service = PhysicalLotBlockingService(UnitDispensingV2Repository(db_session))
    with pytest.raises(NotFoundError):
        await service.set_blocked(
            tenant_id=stock.tenant_id,
            branch_id=stock.branch_id,
            lot_id=uuid4(),
            actor_id=source.incoming.actor_id,
            allowed_branch_ids={stock.branch_id},
            is_blocked=True,
        )
    with pytest.raises(BusinessRuleError):
        await service.set_blocked(
            tenant_id=stock.tenant_id,
            branch_id=stock.branch_id,
            lot_id=source.lot_id,
            actor_id=source.incoming.actor_id,
            allowed_branch_ids={stock.branch_id},
            is_blocked=cast(bool, 1),
        )
    assert await state(db_session, source) == (False, 0)


async def test_existing_command_replay_survives_later_block_without_new_stock_movement(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    lines, command = (cart_line(source, "unit", 3),), uuid4()
    original = await dispense(db_session, source, lines, command_id=command)
    await set_blocked(db_session, source, True)
    before = await facts(db_session, source)
    replay = await dispense(db_session, source, lines, command_id=command)
    assert replay == original
    assert await facts(db_session, source) == before


async def test_sql_rechecks_block_even_when_planner_saw_unblocked_stock(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await receive_source(db_session)
    original = UnitDispensingV2Repository.insert_movement

    async def block_before_insert(
        repo: UnitDispensingV2Repository, movement: UnitStockMovementV2
    ) -> None:
        await set_blocked(repo.session, source, True)
        await original(repo, movement)

    monkeypatch.setattr(UnitDispensingV2Repository, "insert_movement", block_before_insert)
    before = await facts(db_session, source)
    with pytest.raises(DBAPIError):
        await dispense(db_session, source, (cart_line(source, "unit", 3),))
    assert await facts(db_session, source) == before


async def test_blocking_permission_does_not_allow_rewriting_source_quantity_or_price(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    for assignment in (
        "initial_closed_packs=3",
        "purchase_cost_per_pack=999.00",
        "closed_packs=0",
        "blocking_revision=999",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(f"UPDATE unit_stock_lot_v2 SET {assignment} WHERE id=:id"),
                    {"id": source.lot_id},
                )
    assert await remaining(db_session, source) == 60


async def test_block_waits_for_dispensing_commit_and_preserves_delivered_stock(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await receive_source(db_session)
    stock = source.incoming.stock
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        stock,
        permissions=("pos.sell", "batches.view", "batches.update"),
    )
    source = replace(source, incoming=replace(source.incoming, actor_id=actor))
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    await set_receiving_context(db_session, stock, actor)
    delivered = await dispense(db_session, source, (cart_line(source, "unit", 3),))
    entered = asyncio.Event()

    async def competing_block() -> PhysicalLotBlockState:
        async with runtime.factory() as blocker:
            await set_receiving_context(blocker, stock, actor)
            entered.set()
            result = await set_blocked(blocker, source, True)
            await blocker.commit()
            return result

    task = asyncio.create_task(competing_block())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        await db_session.commit()
        result = await asyncio.wait_for(task, timeout=5)
        assert result.is_blocked is True
        assert result.blocking_revision == 1
        await set_receiving_context(db_session, stock, actor)
        assert await remaining(db_session, source) == 57
        assert await state(db_session, source) == (True, 1)
        assert sum(movement.units_taken for movement in delivered.movements) == 3
        with pytest.raises(BusinessRuleError):
            await dispense(db_session, source, (cart_line(source, "unit", 1),))
        assert await remaining(db_session, source) == 57
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await db_session.rollback()


async def test_manager_cannot_block_another_branch_even_with_caller_whitelist(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            branch = await support.scalar(
                text(
                    "INSERT INTO branch (tenant_id,name) VALUES (:tenant,'Other physical branch') "
                    "RETURNING id"
                ),
                {"tenant": runtime.stock.tenant_id},
            )
            assert isinstance(branch, UUID)
    foreign = LedgerContext(runtime.stock.tenant_id, branch, runtime.stock.catalog_id)
    foreign_actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    db_session.info["receiving_runtime"] = ReceivingRuntime(runtime.factory, foreign, foreign_actor)
    await set_receiving_context(db_session, foreign, foreign_actor)
    source = await receive_source(db_session)
    db_session.info["receiving_runtime"] = runtime
    await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
    with pytest.raises(PermissionDeniedError):
        await PhysicalLotBlockingService(UnitDispensingV2Repository(db_session)).set_blocked(
            tenant_id=foreign.tenant_id,
            branch_id=foreign.branch_id,
            lot_id=source.lot_id,
            actor_id=runtime.actor_id,
            allowed_branch_ids={foreign.branch_id},
            is_blocked=True,
        )
    assert (
        await db_session.scalar(
            text("UPDATE unit_stock_lot_v2 SET is_blocked=true WHERE id=:id RETURNING id"),
            {"id": source.lot_id},
        )
        is None
    )
    await set_receiving_context(db_session, foreign, foreign_actor)
    assert await state(db_session, source) == (False, 0)
