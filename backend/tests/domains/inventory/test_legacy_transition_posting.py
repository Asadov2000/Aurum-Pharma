"""Legacy conversion moves one balance atomically through the actual database writer."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_legacy_transition_posting import LegacyTransitionPostingService
from app.domains.inventory.unit_legacy_transition_posting_repository import (
    LegacyTransitionPostingRepository,
)
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    IncomingContext,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.inventory.test_legacy_transition_source import (
    PERMISSIONS,
    accepted_batch,
    all_facts,
    record_count,
)
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    PhysicalSource,
    cart_line,
    dispense,
    remaining,
)
from tests.domains.inventory.test_unit_reconciliation_records_api import increase_quantity
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
POST = text(
    "SELECT * FROM public.post_unit_legacy_transition(:tenant,:branch,:batch,:record,:command)"
)


async def post(
    session: AsyncSession,
    batch_id: UUID,
    record_id: UUID,
    command_id: UUID,
    *,
    tenant_id: UUID | None = None,
    branch_id: UUID | None = None,
) -> dict[str, UUID]:
    context = runtime(session)
    rows = await session.execute(
        POST,
        {
            "tenant": tenant_id or context.stock.tenant_id,
            "branch": branch_id or context.stock.branch_id,
            "batch": batch_id,
            "record": record_id,
            "command": command_id,
        },
    )
    row = rows.mappings().one()
    assert set(row) == {"command_id", "lot_id", "operation_id", "movement_id"}
    result: dict[str, UUID] = {}
    for key, value in row.items():
        assert isinstance(value, UUID)
        result[key] = value
    return result


async def snapshot(session: AsyncSession) -> dict[str, list[object]]:
    result = await all_facts(session)
    for table in (
        "unit_legacy_transition",
        "unit_stock_operation_v2",
        "unit_stock_entry_v2",
        "unit_stock_opened_pack_v2",
        "unit_stock_movement_v2",
        "unit_stock_cart_command_v2",
        "unit_stock_cart_line_v2",
    ):
        result[table] = list(
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
    return result


@pytest.mark.parametrize("cost", [Decimal("0.00"), Decimal("0.01"), Decimal("10.00")])
async def test_post_moves_balance_once_and_preserves_accepted_history(
    db_session: AsyncSession, cost: Decimal
) -> None:
    batch_id = await accepted_batch(db_session, cost)
    record_id = await record_count(db_session, batch_id)
    before = await snapshot(db_session)
    command_id = uuid4()
    result = await post(db_session, batch_id, record_id, command_id)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result["command_id"] == command_id
    assert await db_session.scalar(
        text("SELECT qty_remaining FROM batch WHERE id=:id"), {"id": batch_id}
    ) == Decimal("0.000")
    lot = (
        await db_session.execute(
            text(
                "SELECT closed_packs,initial_closed_packs,purchase_cost_per_pack "
                "FROM unit_stock_lot_v2 WHERE id=:id"
            ),
            {"id": result["lot_id"]},
        )
    ).one()
    assert tuple(lot) == (2, 2, cost)
    assert await db_session.scalar(
        text("SELECT qty_delta FROM batch_movement WHERE id=:id"),
        {"id": result["movement_id"]},
    ) == Decimal("-2.000")
    after = await snapshot(db_session)
    for table in ("incoming_document", "incoming_item", "unit_legacy_reconciliation_record"):
        assert after[table] == before[table]
    assert len(after["unit_stock_lot_v2"]) == 1
    assert len(after["unit_stock_entry_v2"]) == 1
    assert len(after["unit_stock_operation_v2"]) == 1
    assert await post(db_session, batch_id, record_id, command_id) == result
    assert await snapshot(db_session) == after


@pytest.mark.parametrize("change", ["quantity", "price", "branch", "tenant", "record"])
async def test_changed_or_wrong_source_fails_without_partial_stock(
    db_session: AsyncSession, change: str
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    if change == "quantity":
        await increase_quantity(db_session, batch_id)
    elif change == "price":
        await db_session.execute(
            text("UPDATE batch SET purchase_price=11.00 WHERE id=:id"), {"id": batch_id}
        )
    before = await snapshot(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(
                db_session,
                batch_id,
                uuid4() if change == "record" else record_id,
                uuid4(),
                branch_id=uuid4() if change == "branch" else None,
                tenant_id=uuid4() if change == "tenant" else None,
            )
    assert await snapshot(db_session) == before


async def test_opened_count_is_rejected_even_when_quantity_and_value_match(
    db_session: AsyncSession,
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(
        db_session,
        batch_id,
        closed=1,
        opened_packs=[
            {"id": str(uuid4()), "opened_at": "2026-10-15T12:00:00Z", "loose_units": 30},
        ],
    )
    before = await snapshot(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, batch_id, record_id, uuid4())
    assert await snapshot(db_session) == before


@pytest.mark.parametrize("conflict", ["new_command", "other_record", "other_batch"])
async def test_posted_source_and_command_cannot_be_reused_for_another_transition(
    db_session: AsyncSession, conflict: str
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    command_id = uuid4()
    await post(db_session, batch_id, record_id, command_id)
    before = await snapshot(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(
                db_session,
                uuid4() if conflict == "other_batch" else batch_id,
                uuid4() if conflict == "other_record" else record_id,
                uuid4() if conflict == "new_command" else command_id,
            )
    assert await snapshot(db_session) == before


async def test_outer_rollback_restores_both_ledgers_and_allows_clean_retry(
    db_session: AsyncSession,
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    before = await snapshot(db_session)
    command_id = uuid4()
    transaction = await db_session.begin_nested()
    await post(db_session, batch_id, record_id, command_id)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await transaction.rollback()
    assert await snapshot(db_session) == before
    result = await post(db_session, batch_id, record_id, command_id)
    assert result["command_id"] == command_id
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


@pytest.mark.parametrize("missing", PERMISSIONS)
async def test_database_permissions_apply_to_post_and_replay(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    missing: str,
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=tuple(permission for permission in PERMISSIONS if permission != missing),
    )
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    command_id = uuid4()
    await set_receiving_context(db_session, context.stock, other)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, batch_id, record_id, command_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await post(db_session, batch_id, record_id, command_id)
    before = await snapshot(db_session)
    await set_receiving_context(db_session, context.stock, other)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, batch_id, record_id, command_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await snapshot(db_session) == before


async def test_readonly_tenant_cannot_post(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await snapshot(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, batch_id, record_id, uuid4())
    assert await snapshot(db_session) == before


@pytest.mark.parametrize(
    "action",
    [
        "UPDATE unit_legacy_transition SET branch_id=gen_random_uuid()",
        "DELETE FROM unit_legacy_transition",
    ],
)
async def test_transition_journal_is_immutable(db_session: AsyncSession, action: str) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    await post(db_session, batch_id, record_id, uuid4())
    before = await snapshot(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(action + " WHERE tenant_id=:tenant"),
                {"tenant": runtime(db_session).stock.tenant_id},
            )
    assert await snapshot(db_session) == before


async def test_converted_stock_can_be_dispensed_once_and_has_correct_historical_cutoff(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    cutoff = await db_session.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(cutoff, datetime)
    result = await post(db_session, batch_id, record_id, uuid4())
    source_row = (
        await db_session.execute(
            text(
                "SELECT lot.source_item_id,lot.packaging,item.document_id "
                "FROM unit_stock_lot_v2 lot JOIN incoming_item item ON item.id=lot.source_item_id "
                "WHERE lot.id=:id"
            ),
            {"id": result["lot_id"]},
        )
    ).one()
    physical = PhysicalSource(
        IncomingContext(
            context.stock, source_row.document_id, (source_row.source_item_id,), context.actor_id
        ),
        result["lot_id"],
        UnitPackagingV2.model_validate(source_row.packaging),
    )
    assert await remaining(db_session, physical) == 60
    read = text("SELECT * FROM public.pos_stock_on_date_for_report(:tenant,:cutoff,:branch)")
    params = {
        "tenant": context.stock.tenant_id,
        "branch": context.stock.branch_id,
        "cutoff": cutoff,
    }
    old_rows = (await db_session.execute(read, params)).mappings().all()
    assert not any(row["physical_stock"] for row in old_rows)
    await dispense(db_session, physical, (cart_line(physical, "unit", 1),))
    assert await remaining(db_session, physical) == 59
    rows = (
        await db_session.execute(
            read, {**params, "cutoff": datetime.now(UTC) + timedelta(seconds=1)}
        )
    ).mappings().all()
    physical_rows = [row for row in rows if row["physical_stock"]]
    assert len(physical_rows) == 1
    assert physical_rows[0]["physical_stock"]["base_units"] == "59"
    assert physical_rows[0]["value"] == Decimal("19.67")
    assert await db_session.scalar(
        text("SELECT qty_remaining FROM batch WHERE id=:id"), {"id": batch_id}
    ) == Decimal("0.000")


async def test_posted_legacy_batch_cannot_be_revived_by_correction(
    db_session: AsyncSession,
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    await post(db_session, batch_id, record_id, uuid4())
    before = await snapshot(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await InventoryRepository(db_session).insert_movement(
                tenant_id=runtime(db_session).stock.tenant_id,
                batch_id=batch_id,
                movement_type="correction",
                qty_delta=Decimal("1.000"),
            )
    assert await snapshot(db_session) == before


async def test_concurrent_exact_replay_creates_only_one_transfer(db_session: AsyncSession) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    command_id = uuid4()
    await db_session.commit()

    async def attempt() -> dict[str, UUID]:
        async with context.factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            return await post(session, batch_id, record_id, command_id)

    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), timeout=15)
    assert results[0] == results[1]
    await set_receiving_context(db_session, context.stock, context.actor_id)
    for table in ("unit_legacy_transition", "unit_stock_lot_v2", "unit_stock_entry_v2"):
        assert await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        ) == 1


async def test_another_tenant_cannot_read_or_replay_transition(db_session: AsyncSession) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    command_id = uuid4()
    await post(db_session, batch_id, record_id, command_id)
    before = await snapshot(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert await db_session.scalar(text("SELECT count(*) FROM unit_legacy_transition")) == 0
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, batch_id, record_id, command_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await snapshot(db_session) == before


@pytest.mark.parametrize("forged_actor", [False, True])
async def test_internal_facade_uses_actual_actor_and_database_result(
    db_session: AsyncSession, forged_actor: bool
) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    command_id = uuid4()
    service = LegacyTransitionPostingService(LegacyTransitionPostingRepository(db_session))
    before = await snapshot(db_session)
    arguments = {
        "tenant_id": context.stock.tenant_id,
        "branch_id": context.stock.branch_id,
        "batch_id": batch_id,
        "reconciliation_id": record_id,
        "command_id": command_id,
        "actor_id": uuid4() if forged_actor else context.actor_id,
    }
    if forged_actor:
        with pytest.raises(PermissionDeniedError):
            await service.post(**arguments)
        assert await snapshot(db_session) == before
    else:
        result = await service.post(**arguments)
        assert result.model_dump() == await post(db_session, batch_id, record_id, command_id)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO unit_legacy_transition DEFAULT VALUES",
        "SELECT public.assert_unit_legacy_transition_actor(:tenant,:branch)",
        "SELECT public.assert_unit_legacy_transition_complete(:tenant,:branch)",
    ],
)
async def test_app_cannot_bypass_writer_through_private_helpers_or_direct_journal_insert(
    db_session: AsyncSession, statement: str
) -> None:
    context = runtime(db_session)
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await db_session.execute(
                text(statement),
                {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id},
            )
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


async def test_support_cannot_execute_posting_even_with_forged_context(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        support.info["receiving_runtime"] = context
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "SELECT set_config('app.tenant_id',:tenant,true),"
                "set_config('app.user_id',:actor,true)"
            ),
            {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
        )
        with pytest.raises(DBAPIError) as error:
            await post(support, batch_id, record_id, uuid4())
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
        await support.rollback()
