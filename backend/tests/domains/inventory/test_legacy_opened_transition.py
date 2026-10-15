"""Imported opened packs retain their actual state and exact remaining cost."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_legacy_reconciliation_record_repository import (
    LegacyReconciliationRecordRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_record_service import (
    LegacyReconciliationRecordService,
)
from app.domains.inventory.unit_legacy_reconciliation_schemas import LegacyReconciliationSaveRequest
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2
from tests.domains.incoming.test_unit_acceptance import (
    IncomingContext,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_legacy_transition_api import body, path, transition_actor
from tests.domains.inventory.test_legacy_transition_posting import post, snapshot
from tests.domains.inventory.test_legacy_transition_source import PERMISSIONS, accepted_batch
from tests.domains.inventory.test_unit_cost_ledger_v2 import costs
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    PhysicalSource,
    cart_line,
    dispense,
    packs,
    remaining,
)
from tests.domains.inventory.test_unit_reconciliation_records_api import increase_quantity
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
OPENING_TABLE = "unit_stock_opening_pack_v2"


@dataclass(frozen=True)
class PartialSource:
    batch_id: UUID
    record_id: UUID
    pack_ids: tuple[UUID, ...]


def opened(units: int, *, pack_id: UUID | None = None) -> dict[str, object]:
    return {
        "id": str(pack_id or uuid4()),
        "opened_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "intact_blisters": [],
        "opened_blisters": [],
        "loose_units": units,
    }


async def partial_source(
    session: AsyncSession,
    *,
    quantity: str = "1.500",
    closed: int = 1,
    price: Decimal = Decimal("10.00"),
    observed: list[dict[str, object]] | None = None,
    blister: int | None = None,
) -> PartialSource:
    context = runtime(session)
    batch_id = await accepted_batch(session, price)
    delta = Decimal(quantity) - Decimal("2.000")
    if delta:
        await InventoryRepository(session).insert_movement(
            tenant_id=context.stock.tenant_id,
            batch_id=batch_id,
            movement_type="correction",
            qty_delta=delta,
        )
    observed = observed if observed is not None else [opened(15)]
    request = LegacyReconciliationSaveRequest.model_validate(
        {
            "branch_id": context.stock.branch_id,
            "reconciliation_id": uuid4(),
            "expected_legacy_quantity": quantity,
            "packaging": {
                "version": 2,
                "unit": "tablet",
                "units_per_pack": 30,
                "units_per_blister": blister,
                "dispense_step": 1,
            },
            "closed_packs": closed,
            "opened_packs": observed,
        }
    )
    saved = await LegacyReconciliationRecordService(
        LegacyReconciliationRecordRepository(session)
    ).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        batch_id=batch_id,
        request=request,
        view_branch_ids={context.stock.branch_id},
        update_branch_ids={context.stock.branch_id},
    )
    return PartialSource(batch_id, saved.id, tuple(pack.id for pack in request.opened_packs))


async def all_facts(session: AsyncSession) -> dict[str, list[object]]:
    facts = await snapshot(session)
    facts[OPENING_TABLE] = list(
        (
            await session.execute(
                text(
                    f"SELECT to_jsonb(row) FROM {OPENING_TABLE} row "
                    "WHERE tenant_id=:tenant ORDER BY to_jsonb(row)::text"
                ),
                {"tenant": runtime(session).stock.tenant_id},
            )
        ).scalars()
    )
    return facts


async def physical_source(session: AsyncSession, lot_id: UUID) -> PhysicalSource:
    context = runtime(session)
    row = (
        await session.execute(
            text(
                "SELECT lot.source_item_id,lot.packaging,item.document_id "
                "FROM unit_stock_lot_v2 lot JOIN incoming_item item ON item.id=lot.source_item_id "
                "WHERE lot.id=:id"
            ),
            {"id": lot_id},
        )
    ).one()
    return PhysicalSource(
        IncomingContext(context.stock, row.document_id, (row.source_item_id,), context.actor_id),
        lot_id,
        UnitPackagingV2.model_validate(row.packaging),
    )


async def report(session: AsyncSession, cutoff: datetime) -> list[dict[str, object]]:
    context = runtime(session)
    rows = await session.execute(
        text("SELECT * FROM public.pos_stock_on_date_for_report(:tenant,:cutoff,:branch)"),
        {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id, "cutoff": cutoff},
    )
    return [dict(row) for row in rows.mappings()]


@pytest.mark.parametrize("price", [Decimal("0.00"), Decimal("10.00")])
async def test_opening_is_an_exact_initial_basis_not_fictitious_past_dispensing(
    db_session: AsyncSession, price: Decimal
) -> None:
    observed = [opened(15)]
    source = await partial_source(db_session, price=price, observed=observed)
    before = await all_facts(db_session)
    result = await post(db_session, source.batch_id, source.record_id, uuid4())
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    physical = await physical_source(db_session, result["lot_id"])
    assert await remaining(db_session, physical) == 45
    lot = (
        await db_session.execute(
            text(
                "SELECT initial_closed_packs,closed_packs,initial_opened_packs,"
                "initial_opened_units,initial_opened_cost FROM unit_stock_lot_v2 WHERE id=:id"
            ),
            {"id": result["lot_id"]},
        )
    ).one()
    assert tuple(lot) == (1, 1, 1, 15, price / 2)
    assert await db_session.scalar(
        text("SELECT qty_remaining FROM batch WHERE id=:id"), {"id": source.batch_id}
    ) == Decimal("0.000")
    projection = await packs(db_session, physical)
    assert projection == [
        (source.pack_ids[0], {"intact_blisters": [], "opened_blisters": [], "loose_units": 15}, 15)
    ]
    opening = (
        await db_session.execute(
            text(
                f"SELECT id,pack_ordinal,opened_at,cost_amount FROM {OPENING_TABLE} "
                "WHERE lot_id=:id"
            ),
            {"id": result["lot_id"]},
        )
    ).one()
    assert opening.id == source.pack_ids[0] and opening.pack_ordinal == 1
    assert opening.opened_at == datetime.fromisoformat(str(observed[0]["opened_at"]))
    assert opening.cost_amount == price / 2
    after = await all_facts(db_session)
    for table in ("incoming_document", "incoming_item", "unit_legacy_reconciliation_record"):
        assert after[table] == before[table]
    assert after["unit_stock_movement_v2"] == []
    assert after["unit_stock_cart_command_v2"] == after["unit_stock_cart_line_v2"] == []


async def test_zero_closed_pack_source_preserves_multiple_uuid_sorted_openings(
    db_session: AsyncSession,
) -> None:
    low, high = UUID(int=901), UUID(int=902)
    source = await partial_source(
        db_session,
        quantity="1.000",
        closed=0,
        observed=[opened(15, pack_id=high), opened(15, pack_id=low)],
    )
    result = await post(db_session, source.batch_id, source.record_id, uuid4())
    physical = await physical_source(db_session, result["lot_id"])
    assert await remaining(db_session, physical) == 30
    assert [row[0] for row in await packs(db_session, physical)] == [low, high]
    rows = (
        await db_session.execute(
            text(
                f"SELECT id,pack_ordinal,cost_amount FROM {OPENING_TABLE} "
                "WHERE lot_id=:id ORDER BY pack_ordinal"
            ),
            {"id": result["lot_id"]},
        )
    ).all()
    assert [tuple(row) for row in rows] == [(low, 1, Decimal("5.00")), (high, 2, Decimal("5.00"))]
    taken = await dispense(db_session, physical, (cart_line(physical, "unit", 30),))
    assert (await costs(db_session, physical, taken.command_id)).total_amount == Decimal("10.00")
    assert await remaining(db_session, physical) == 0


async def test_closed_and_opened_ordinals_never_overlap_and_costs_telescope(
    db_session: AsyncSession,
) -> None:
    source = await partial_source(db_session)
    cutoff = await db_session.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(cutoff, datetime)
    result = await post(db_session, source.batch_id, source.record_id, uuid4())
    physical = await physical_source(db_session, result["lot_id"])
    opening_before = (await all_facts(db_session))[OPENING_TABLE]
    old_rows = await report(db_session, cutoff)
    assert len(old_rows) == 1 and old_rows[0]["value"] == Decimal("15.00")
    assert not old_rows[0]["physical_stock"]
    amounts = []
    ordinals = []
    offsets = []
    for sale_format, quantity in (("unit", 1), ("pack", 1), ("unit", 14)):
        taken = await dispense(db_session, physical, (cart_line(physical, sale_format, quantity),))
        priced = await costs(db_session, physical, taken.command_id)
        amounts.append(priced.total_amount)
        ordinals.extend(part.pack_ordinal_start for part in priced.cost_slices)
        offsets.extend(part.unit_offset for part in priced.cost_slices)
        current = await report(db_session, datetime.now(UTC) + timedelta(seconds=1))
        physical_rows = [row for row in current if row["physical_stock"]]
        if quantity == 1 and sale_format == "unit":
            assert len(physical_rows) == 1
            assert physical_rows[0]["value"] == Decimal("14.67")
            stock = physical_rows[0]["physical_stock"]
            assert isinstance(stock, dict) and stock["base_units"] == "44"
    assert amounts == [Decimal("0.33"), Decimal("10.00"), Decimal("4.67")]
    assert ordinals == [1, 2, 1] and offsets == [15, 0, 16]
    assert sum(amounts) == Decimal("15.00")
    assert await remaining(db_session, physical) == 0
    assert (await all_facts(db_session))[OPENING_TABLE] == opening_before


async def test_intact_blisters_and_opened_fragment_survive_initialization(
    db_session: AsyncSession,
) -> None:
    pack = opened(0)
    pack.update(
        {"intact_blisters": [3, 1], "opened_blisters": [{"number": 2, "remaining_units": 4}]}
    )
    source = await partial_source(
        db_session,
        quantity="0.800",
        closed=0,
        observed=[pack],
        blister=10,
    )
    result = await post(db_session, source.batch_id, source.record_id, uuid4())
    physical = await physical_source(db_session, result["lot_id"])
    projection = await packs(db_session, physical)
    assert projection[0][0] == source.pack_ids[0] and projection[0][2] == 24
    intact = projection[0][1]["intact_blisters"]
    assert isinstance(intact, list) and set(intact) == {1, 3}
    assert projection[0][1]["opened_blisters"] == [{"number": 2, "remaining_units": 4}]
    total = Decimal("0.00")
    for sale_format, quantity in (("blister", 1), ("unit", 4), ("blister", 1)):
        taken = await dispense(db_session, physical, (cart_line(physical, sale_format, quantity),))
        priced = await costs(db_session, physical, taken.command_id)
        total += priced.total_amount
        assert all(part.pack_ordinal_start == 1 for part in priced.cost_slices)
    assert total == Decimal("8.00") and await remaining(db_session, physical) == 0


@pytest.mark.parametrize("failure", ["subcent", "tail_sum", "stale_quantity"])
async def test_incompatible_or_stale_opened_count_leaves_every_balance_unchanged(
    db_session: AsyncSession, failure: str
) -> None:
    source = await partial_source(
        db_session,
        quantity="0.500" if failure == "subcent" else "1.000",
        closed=0,
        price=(
            Decimal("7.31")
            if failure == "subcent"
            else Decimal("0.01") if failure == "tail_sum" else Decimal("10.00")
        ),
        observed=[opened(15)] if failure == "subcent" else [opened(15), opened(15)],
    )
    if failure == "stale_quantity":
        await increase_quantity(db_session, source.batch_id)
    before = await all_facts(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, source.batch_id, source.record_id, uuid4())
    assert await all_facts(db_session) == before


@pytest.mark.parametrize(
    "opened_at",
    ["0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59"],
)
async def test_opening_time_outside_python_utc_range_is_rejected_atomically(
    db_session: AsyncSession, opened_at: str
) -> None:
    observed = opened(15)
    observed["opened_at"] = opened_at
    source = await partial_source(db_session, observed=[observed])
    before = await all_facts(db_session)
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await post(db_session, source.batch_id, source.record_id, uuid4())
    assert getattr(error.value.orig, "sqlstate", None) == "23514"
    assert await all_facts(db_session) == before


async def test_outer_rollback_removes_opening_basis_and_allows_same_command_retry(
    db_session: AsyncSession,
) -> None:
    source = await partial_source(db_session)
    command_id = uuid4()
    before = await all_facts(db_session)
    transaction = await db_session.begin_nested()
    await post(db_session, source.batch_id, source.record_id, command_id)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await transaction.rollback()
    assert await all_facts(db_session) == before
    result = await post(db_session, source.batch_id, source.record_id, command_id)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    after = await all_facts(db_session)
    assert await post(db_session, source.batch_id, source.record_id, command_id) == result
    assert await all_facts(db_session) == after


async def test_exact_replay_keeps_opening_basis_after_stock_is_exhausted(
    db_session: AsyncSession,
) -> None:
    source = await partial_source(db_session)
    command_id = uuid4()
    result = await post(db_session, source.batch_id, source.record_id, command_id)
    physical = await physical_source(db_session, result["lot_id"])
    await dispense(db_session, physical, (cart_line(physical, "unit", 45),))
    before = await all_facts(db_session)
    assert await post(db_session, source.batch_id, source.record_id, command_id) == result
    assert await all_facts(db_session) == before
    assert len(before[OPENING_TABLE]) == 1


async def test_opened_transition_concurrent_replay_creates_one_initialization(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    source = await partial_source(db_session)
    command_id = uuid4()
    await db_session.commit()

    async def attempt() -> dict[str, UUID]:
        async with context.factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            return await post(session, source.batch_id, source.record_id, command_id)

    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), timeout=15)
    assert results[0] == results[1]
    await set_receiving_context(db_session, context.stock, context.actor_id)
    after = await all_facts(db_session)
    assert len(after[OPENING_TABLE]) == len(after["unit_legacy_transition"]) == 1


@pytest.mark.parametrize("missing", PERMISSIONS)
async def test_opened_post_and_replay_recheck_real_database_permissions(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine, missing: str
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=tuple(permission for permission in PERMISSIONS if permission != missing),
    )
    source = await partial_source(db_session)
    command_id = uuid4()
    before = await all_facts(db_session)
    await set_receiving_context(db_session, context.stock, other)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, source.batch_id, source.record_id, command_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await all_facts(db_session) == before
    await post(db_session, source.batch_id, source.record_id, command_id)
    after = await all_facts(db_session)
    await set_receiving_context(db_session, context.stock, other)
    if missing == "batches.view_costs":
        assert await db_session.scalar(text(f"SELECT count(*) FROM {OPENING_TABLE}")) == 0
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, source.batch_id, source.record_id, command_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await all_facts(db_session) == after


async def test_foreign_tenant_cannot_read_opening_cost_or_replay_transition(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    source = await partial_source(db_session)
    command_id = uuid4()
    await post(db_session, source.batch_id, source.record_id, command_id)
    before = await all_facts(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert await db_session.scalar(text(f"SELECT count(*) FROM {OPENING_TABLE}")) == 0
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, source.batch_id, source.record_id, command_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await all_facts(db_session) == before


@pytest.mark.parametrize("action", ["INSERT", "UPDATE", "DELETE"])
async def test_app_cannot_forge_or_rewrite_private_opening_history(
    db_session: AsyncSession, action: str
) -> None:
    source = await partial_source(db_session)
    await post(db_session, source.batch_id, source.record_id, uuid4())
    before = await all_facts(db_session)
    statements = {
        "INSERT": f"INSERT INTO {OPENING_TABLE} DEFAULT VALUES",
        "UPDATE": f"UPDATE {OPENING_TABLE} SET cost_amount=0",
        "DELETE": f"DELETE FROM {OPENING_TABLE}",
    }
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await db_session.execute(text(statements[action]))
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    assert await all_facts(db_session) == before


async def test_readonly_recovery_returns_only_the_same_public_identifiers(
    client: AsyncClient, db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    source = await partial_source(db_session)
    request = body(db_session, source.record_id)
    async with transition_actor(db_session):
        response = await client.post(path(source.batch_id), json=request)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"command_id", "lot_id", "operation_id", "movement_id"}
    assert_no_cost_fields(payload)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await all_facts(db_session)
    async with transition_actor(db_session):
        recovered = await client.get(
            f"{path(source.batch_id)}/{request['command_id']}",
            params={"branch_id": request["branch_id"]},
        )
        denied = await client.post(path(source.batch_id), json=request)
    assert recovered.status_code == 200 and recovered.json() == payload
    assert recovered.headers["cache-control"] == "private, no-store"
    assert denied.status_code == 422
    assert await all_facts(db_session) == before


@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
async def test_owner_cannot_rewrite_or_delete_opening_cost_history(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, action: str
) -> None:
    context = runtime(db_session)
    source = await partial_source(db_session)
    result = await post(db_session, source.batch_id, source.record_id, uuid4())
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await all_facts(db_session)
    statement = (
        f"UPDATE {OPENING_TABLE} SET cost_amount=0 WHERE lot_id=:lot"
        if action == "UPDATE"
        else f"DELETE FROM {OPENING_TABLE} WHERE lot_id=:lot"
    )
    with pytest.raises(DBAPIError) as error:
        async with maintenance_engine.begin() as owner:
            await owner.execute(text(statement), {"lot": result["lot_id"]})
    assert getattr(error.value.orig, "sqlstate", None) == "55000"
    assert await all_facts(db_session) == before


async def test_raw_audit_snapshots_never_store_opening_costs(db_session: AsyncSession) -> None:
    source = await partial_source(db_session)
    result = await post(db_session, source.batch_id, source.record_id, uuid4())
    rows = (
        await db_session.execute(
            text(
                "SELECT table_name,new_values FROM audit_log WHERE "
                "(table_name='unit_stock_lot_v2' AND new_values->>'id'=:lot) OR "
                "(table_name=:opening AND new_values->>'id'=:pack)"
            ),
            {
                "lot": str(result["lot_id"]),
                "opening": OPENING_TABLE,
                "pack": str(source.pack_ids[0]),
            },
        )
    ).all()
    assert {row.table_name for row in rows} == {"unit_stock_lot_v2", OPENING_TABLE}
    for row in rows:
        assert "initial_opened_cost" not in row.new_values
        if row.table_name == OPENING_TABLE:
            assert "cost_amount" not in row.new_values
