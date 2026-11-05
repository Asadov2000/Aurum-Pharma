"""Physical cart posting cannot bypass actor scope or rewrite stock projections."""

from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_dispensing_v2_service import PhysicalUnitLedgerV2Service
from app.domains.inventory.unit_ledger_v2_models import UnitStockMovementV2
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    DISPENSING_TABLES,
    cart_line,
    dispense,
    receive_source,
    remaining,
)

db_session = receiving_session_fixture


async def test_cashier_physical_result_never_contains_financial_snapshot(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, source.incoming.stock, permissions=("pos.sell",)
    )
    employee_source = replace(source, incoming=replace(source.incoming, actor_id=actor))
    await set_receiving_context(db_session, source.incoming.stock, actor)
    result = await dispense(db_session, employee_source, (cart_line(source, "unit", 3),))
    for movement in result.movements:
        assert not any(key.startswith("cost_") for key in asdict(movement))
    # Financial posting needs this internal source even when the cashier cannot
    # view procurement prices; its values are not part of the physical response.
    financial = await PhysicalUnitLedgerV2Service(
        UnitDispensingV2Repository(db_session)
    ).cost_result(
        tenant_id=source.incoming.stock.tenant_id,
        branch_id=source.incoming.stock.branch_id,
        command_id=result.command_id,
        actor_id=actor,
        allowed_branch_ids={source.incoming.stock.branch_id},
    )
    assert financial.total_amount > 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("cost_pack_ordinal_start", 2),
        ("cost_pack_count", 2),
        ("cost_unit_offset", 1),
        ("cost_base_units", 4),
        ("cost_original_pack_cost", Decimal("999.00")),
        ("cost_amount", Decimal("999.00")),
        ("cost_version", 2),
    ],
)
async def test_forged_cost_snapshot_cancels_the_entire_physical_cart(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: int | Decimal,
) -> None:
    source = await receive_source(db_session)
    insert_movement = UnitDispensingV2Repository.insert_movement

    async def insert_forged(
        repo: UnitDispensingV2Repository, movement: UnitStockMovementV2
    ) -> None:
        if movement.units_taken > 0:
            setattr(movement, field, value)
        await insert_movement(repo, movement)

    monkeypatch.setattr(UnitDispensingV2Repository, "insert_movement", insert_forged)
    with pytest.raises(DBAPIError):
        await dispense(db_session, source, (cart_line(source, "unit", 3),))
    assert await remaining(db_session, source) == 60
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_cart_command_v2 WHERE tenant_id=:tenant"),
            {"tenant": source.incoming.stock.tenant_id},
        )
        == 0
    )


async def test_app_can_lock_its_stock_but_cannot_update_even_the_lock_permission_column(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 3),))
    for table in ("unit_stock_lot_v2", "unit_stock_opened_pack_v2"):
        assert (
            await db_session.execute(
                text(f"SELECT id FROM {table} WHERE tenant_id=:tenant FOR UPDATE"),
                {"tenant": source.incoming.stock.tenant_id},
            )
        ).first() is not None
        for assignment in ("updated_at=now()", "updated_by=NULL"):
            with pytest.raises(DBAPIError):
                async with db_session.begin_nested():
                    await db_session.execute(
                        text(f"UPDATE {table} SET {assignment} WHERE tenant_id=:tenant"),
                        {"tenant": source.incoming.stock.tenant_id},
                    )


async def test_cart_history_and_physical_states_are_immutable_outside_journal_triggers(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 3),))
    for table, assignment in (
        ("unit_stock_cart_command_v2", "request_hash=repeat('0',64)"),
        ("unit_stock_cart_line_v2", "quantity=4"),
        ("unit_stock_opened_pack_v2", "remaining_units=30"),
        ("unit_stock_opened_pack_v2", "state=jsonb_set(state,'{loose_units}','30')"),
        ("unit_stock_movement_v2", "units_taken=4"),
        ("unit_stock_movement_v2", "cost_amount=0"),
        ("unit_stock_movement_v2", "cost_version=NULL"),
    ):
        for statement in (
            f"UPDATE {table} SET {assignment} WHERE tenant_id=:tenant",
            f"DELETE FROM {table} WHERE tenant_id=:tenant",
        ):
            with pytest.raises(DBAPIError):
                async with db_session.begin_nested():
                    await db_session.execute(
                        text(statement), {"tenant": source.incoming.stock.tenant_id}
                    )
    assert await remaining(db_session, source) == 57


@pytest.mark.parametrize(
    "tamper",
    ["units", "closed_delta", "null_state", "duplicate", "overlap", "extra", "loose", "unsorted"],
)
async def test_forged_sql_transition_rolls_back_command_and_opening(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    source = await receive_source(db_session)
    insert_movement = UnitDispensingV2Repository.insert_movement

    async def insert_forged(
        repo: UnitDispensingV2Repository, movement: UnitStockMovementV2
    ) -> None:
        if movement.kind == "unit_take":
            if tamper == "units":
                movement.units_taken += 1
            elif tamper == "closed_delta":
                movement.closed_delta = -1
            elif tamper == "null_state":
                movement.after_state = None
            else:
                assert movement.after_state is not None
                state = dict(movement.after_state)
                if tamper == "duplicate":
                    state["intact_blisters"] = [2, 2, 3]
                elif tamper == "unsorted":
                    state["intact_blisters"] = [3, 2]
                elif tamper == "overlap":
                    state["intact_blisters"] = [1, 2, 3]
                elif tamper == "extra":
                    state["unexpected"] = True
                else:
                    state["loose_units"] = 3
                movement.after_state = state
        await insert_movement(repo, movement)

    monkeypatch.setattr(UnitDispensingV2Repository, "insert_movement", insert_forged)
    with pytest.raises(DBAPIError):
        await dispense(db_session, source, (cart_line(source, "unit", 3),))
    assert await remaining(db_session, source) == 60
    for table in DISPENSING_TABLES:
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": source.incoming.stock.tenant_id},
            )
            == 0
        )


async def test_orphan_command_cannot_be_saved_without_lines_and_movements(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO unit_stock_cart_command_v2 "
                    "(tenant_id,id,branch_id,actor_id,request_hash,created_by) "
                    "VALUES (:tenant,:id,:branch,:actor,:hash,:actor)"
                ),
                {
                    "tenant": source.incoming.stock.tenant_id,
                    "id": uuid4(),
                    "branch": source.incoming.stock.branch_id,
                    "actor": source.incoming.actor_id,
                    "hash": "a" * 64,
                },
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await remaining(db_session, source) == 60


@pytest.mark.parametrize("foreign_tenant", [False, True])
async def test_foreign_scope_hides_every_cart_table_and_cannot_lock_stock(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    foreign_tenant: bool,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            if foreign_tenant:
                foreign = await seed_ledger_context(support)
            else:
                branch_id = await support.scalar(
                    text(
                        "INSERT INTO branch (tenant_id,name) "
                        "VALUES (:tenant,'Foreign cart branch') RETURNING id"
                    ),
                    {"tenant": runtime.stock.tenant_id},
                )
                assert isinstance(branch_id, UUID)
                foreign = LedgerContext(
                    runtime.stock.tenant_id, branch_id, runtime.stock.catalog_id
                )
    actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    db_session.info["receiving_runtime"] = ReceivingRuntime(runtime.factory, foreign, actor)
    await set_receiving_context(db_session, foreign, actor)
    source = await receive_source(db_session)
    result = await dispense(db_session, source, (cart_line(source, "unit", 3),))
    await db_session.commit()
    db_session.info["receiving_runtime"] = runtime
    await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
    for table in DISPENSING_TABLES:
        column = "id" if table == "unit_stock_cart_command_v2" else "command_id"
        if table == "unit_stock_opened_pack_v2":
            column, identifier = "lot_id", source.lot_id
        else:
            identifier = result.command_id
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {column}=:id"), {"id": identifier}
            )
            == 0
        )
    for table in ("unit_stock_lot_v2", "unit_stock_opened_pack_v2"):
        column = "id" if table == "unit_stock_lot_v2" else "lot_id"
        assert (
            await db_session.execute(
                text(f"SELECT id FROM {table} WHERE {column}=:id FOR UPDATE"), {"id": source.lot_id}
            )
        ).first() is None
    with pytest.raises(PermissionDeniedError):
        await PhysicalUnitLedgerV2Service(UnitDispensingV2Repository(db_session)).dispense_cart(
            tenant_id=foreign.tenant_id,
            branch_id=foreign.branch_id,
            actor_id=runtime.actor_id,
            allowed_branch_ids={foreign.branch_id},
            command_id=uuid4(),
            lines=(cart_line(source, "unit", 1),),
        )


async def test_employee_without_sale_permission_cannot_post_or_replay_cart(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    lines = (cart_line(source, "unit", 3),)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        source.incoming.stock,
        permissions=("incoming.view", "batches.view"),
    )
    employee_source = replace(source, incoming=replace(source.incoming, actor_id=actor))
    await set_receiving_context(db_session, source.incoming.stock, actor)
    with pytest.raises(PermissionDeniedError):
        await dispense(db_session, employee_source, lines)
    await set_receiving_context(db_session, source.incoming.stock, source.incoming.actor_id)
    result = await dispense(db_session, source, lines)
    await set_receiving_context(db_session, source.incoming.stock, actor)
    with pytest.raises(PermissionDeniedError):
        await dispense(db_session, employee_source, lines, command_id=result.command_id)
    with pytest.raises(PermissionDeniedError):
        await PhysicalUnitLedgerV2Service(UnitDispensingV2Repository(db_session)).cost_result(
            tenant_id=source.incoming.stock.tenant_id,
            branch_id=source.incoming.stock.branch_id,
            command_id=result.command_id,
            actor_id=actor,
            allowed_branch_ids={source.incoming.stock.branch_id},
        )


async def test_support_cannot_post_or_mutate_even_when_spoofing_the_actor(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    source = await receive_source(db_session)
    lines = (cart_line(source, "unit", 3),)
    await db_session.commit()
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
        with pytest.raises(PermissionDeniedError):
            await dispense(support, source, lines)
        await support.rollback()
    await set_receiving_context(db_session, source.incoming.stock, source.incoming.actor_id)
    await dispense(db_session, source, lines)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        for table in ("unit_stock_lot_v2", "unit_stock_opened_pack_v2"):
            with pytest.raises(DBAPIError):
                async with support.begin_nested():
                    await support.execute(
                        text(f"UPDATE {table} SET updated_at=now() WHERE tenant_id=:tenant"),
                        {"tenant": source.incoming.stock.tenant_id},
                    )
        await support.rollback()


async def test_physical_posting_audits_all_components_and_keeps_purchase_cost_private(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 3),))
    tables = set(
        (
            await db_session.execute(
                text(
                    "SELECT DISTINCT table_name FROM audit_log "
                    "WHERE tenant_id=:tenant AND table_name=ANY(:tables)"
                ),
                {"tenant": source.incoming.stock.tenant_id, "tables": list(DISPENSING_TABLES)},
            )
        ).scalars()
    )
    assert tables == set(DISPENSING_TABLES)
    records = (
        (
            await db_session.execute(
                text(
                    "SELECT new_values->>'purchase_cost_per_pack' FROM audit_log "
                    "WHERE tenant_id=:tenant AND table_name='unit_stock_lot_v2'"
                ),
                {"tenant": source.incoming.stock.tenant_id},
            )
        )
        .scalars()
        .all()
    )
    assert records and set(records) == {"***"}
    cost_records = (
        await db_session.execute(
            text(
                "SELECT new_values->>'cost_original_pack_cost',new_values->>'cost_amount' "
                "FROM audit_log WHERE tenant_id=:tenant AND table_name='unit_stock_movement_v2'"
            ),
            {"tenant": source.incoming.stock.tenant_id},
        )
    ).all()
    assert cost_records and ("***", "***") in cost_records
    assert set(cost_records) <= {("***", "***"), (None, None)}
