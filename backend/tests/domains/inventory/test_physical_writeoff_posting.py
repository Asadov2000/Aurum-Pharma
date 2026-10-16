"""Standalone physical write-offs preserve real stock, cost and recovery authority."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import BusinessRuleError
from app.domains.inventory.unit_stock_v2 import OpenedPackV2, UnitPackagingV2, UnitStockBatchV2
from app.domains.inventory.unit_writeoff_v2 import (
    PhysicalWriteOffSelectionV2,
    plan_physical_writeoff_v2,
)
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance_v2 import accept_v2, seed_v2_incoming
from tests.domains.inventory.test_legacy_opened_transition import (
    all_facts,
    partial_source,
    physical_source,
    report,
)
from tests.domains.inventory.test_legacy_transition_posting import post as post_transition
from tests.domains.inventory.test_physical_inventory_api import inventory_actor
from tests.domains.inventory.test_unit_cost_ledger_v2 import costs, priced_source
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    PhysicalSource,
    cart_line,
    dispense,
    packs,
    receive_source,
    remaining,
)
from tests.domains.inventory.test_unit_physical_blocking import set_blocked
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
PATH = "/api/v1/batches/physical/write-offs"
PERMISSIONS = ("batches.view", "batches.write_off")
TABLES = ("physical_writeoff_command", "physical_writeoff_entry")


def selection(
    *,
    closed: int = 0,
    pack_id: UUID | None = None,
    blisters: tuple[tuple[int, int], ...] = (),
    loose: int = 0,
) -> dict[str, object]:
    return {
        "closed_packs": closed,
        "opened_packs": (
            [
                {
                    "pack_id": str(pack_id),
                    "blisters": [{"number": n, "units": u} for n, u in blisters],
                    "loose_units": loose,
                }
            ]
            if pack_id is not None
            else []
        ),
    }


async def post(
    session: AsyncSession,
    source: PhysicalSource,
    selected: dict[str, object],
    *,
    operation_id: UUID | None = None,
    reason: str = "damaged",
    comment: str | None = None,
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
    branch_id: UUID | None = None,
) -> dict[str, object]:
    context = runtime(session)
    result = await session.scalar(
        text(
            "SELECT public.record_physical_writeoff(:tenant,:branch,:operation,:actor,:lot,"
            ":reason,:comment,CAST(:selection AS jsonb))"
        ),
        {
            "tenant": tenant_id or context.stock.tenant_id,
            "branch": branch_id or source.incoming.stock.branch_id,
            "operation": operation_id or uuid4(),
            "actor": actor_id or context.actor_id,
            "lot": source.lot_id,
            "reason": reason,
            "comment": comment,
            "selection": json.dumps(selected),
        },
    )
    assert isinstance(result, dict)
    return result


async def status(session: AsyncSession, operation_id: UUID) -> dict[str, object] | None:
    context = runtime(session)
    result = await session.scalar(
        text("SELECT public.physical_writeoff_status(:tenant,:branch,:operation)"),
        {
            "tenant": context.stock.tenant_id,
            "branch": context.stock.branch_id,
            "operation": operation_id,
        },
    )
    assert result is None or isinstance(result, dict)
    return result


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await all_facts(session)
    for table in ("sale", "sale_item", "sale_payment", "pos_physical_checkout"):
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


@asynccontextmanager
async def writeoff_actor(session: AsyncSession) -> AsyncIterator[CurrentUser]:
    async with inventory_actor(session) as actor:
        actor.permissions = set(PERMISSIONS)
        actor.permission_scopes = {
            code: frozenset({runtime(session).stock.branch_id}) for code in PERMISSIONS
        }
        yield actor


def request(source: PhysicalSource, operation_id: UUID | None = None) -> dict[str, object]:
    return {
        "operation_id": str(operation_id or uuid4()),
        "branch_id": str(source.incoming.stock.branch_id),
        "lot_id": str(source.lot_id),
        "reason": "damaged",
        "comment": "Broken carton",
        "selection": selection(closed=1),
    }


async def denied_http(
    client: AsyncClient,
    session: AsyncSession,
    method: str,
    url: str,
    *,
    payload: dict[str, object] | None = None,
    branch_id: object = None,
) -> Response:
    savepoint = await session.begin_nested()
    try:
        if method == "POST":
            return await client.post(url, json=payload)
        return await client.get(url, params={"branch_id": str(branch_id)})
    finally:
        await savepoint.rollback()


async def test_mixed_writeoff_is_independent_of_sales_and_replay_does_not_repeat_stock(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session, quantity=2)
    await dispense(db_session, source, (cart_line(source, "unit", 1),))
    pack_id = (await packs(db_session, source))[0][0]
    selected = selection(closed=1, pack_id=pack_id, blisters=((1, 3),))
    before = await facts(db_session)
    operation_id = uuid4()
    result = await post(
        db_session, source, selected, operation_id=operation_id, comment="Broken carton"
    )
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result == {
        "operation_id": str(operation_id),
        "branch_id": str(source.incoming.stock.branch_id),
        "lot_id": str(source.lot_id),
        "reason": "damaged",
        "comment": "Broken carton",
        "base_units": "33",
        "closed_packs": "1",
        "created_at": result["created_at"],
    }
    assert isinstance(result["created_at"], str)
    assert_no_cost_fields(result)
    assert await remaining(db_session, source) == 26
    current = (await packs(db_session, source))[0]
    assert current[0] == pack_id and current[2] == 26
    assert current[1]["intact_blisters"] == [2, 3]
    assert current[1]["opened_blisters"] == [{"number": 1, "remaining_units": 6}]
    after = await facts(db_session)
    for table in (
        "incoming_document",
        "incoming_item",
        "unit_stock_cart_command_v2",
        "unit_stock_cart_line_v2",
        "unit_stock_movement_v2",
        "sale",
        "sale_item",
        "sale_payment",
        "pos_physical_checkout",
    ):
        assert after[table] == before[table]
    assert await post(
        db_session, source, selected, operation_id=operation_id, comment="Broken carton"
    ) == result
    assert await status(db_session, operation_id) == result
    assert await facts(db_session) == after


@pytest.mark.parametrize("price", [Decimal("0.00"), Decimal("0.01"), Decimal("10.00")])
async def test_sealed_writeoff_advances_the_real_sealed_ordinal_and_retains_exact_cost(
    db_session: AsyncSession,
    price: Decimal,
) -> None:
    source = await priced_source(db_session, quantity=2, price=price)
    await post(db_session, source, selection(closed=1))
    assert await remaining(db_session, source) == 30
    taken = await dispense(db_session, source, (cart_line(source, "pack", 1),))
    priced = await costs(db_session, source, taken.command_id)
    assert priced.total_amount == price
    assert priced.cost_slices[0].pack_ordinal_start == 2
    assert await remaining(db_session, source) == 0


@pytest.mark.parametrize("blister", [None, 10])
async def test_partial_writeoff_then_sale_never_reuses_or_loses_cost_cents(
    db_session: AsyncSession,
    blister: int | None,
) -> None:
    source = await priced_source(db_session, blister=blister)
    first = await dispense(db_session, source, (cart_line(source, "unit", 1),))
    assert (await costs(db_session, source, first.command_id)).total_amount == Decimal("0.33")
    pack_id = (await packs(db_session, source))[0][0]
    cutoff = await db_session.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(cutoff, datetime)
    selected = (
        selection(pack_id=pack_id, loose=1)
        if blister is None
        else selection(pack_id=pack_id, blisters=((1, 1),))
    )
    await post(db_session, source, selected)
    old = await report(db_session, cutoff)
    assert len(old) == 1 and old[0]["value"] == Decimal("9.67")
    current = await report(db_session, datetime.now(UTC) + timedelta(seconds=1))
    assert len(current) == 1 and current[0]["value"] == Decimal("9.34")
    taken = await dispense(db_session, source, (cart_line(source, "unit", 1),))
    priced = await costs(db_session, source, taken.command_id)
    assert priced.total_amount == Decimal("0.34")
    assert priced.cost_slices[0].unit_offset == 2
    last = await dispense(db_session, source, (cart_line(source, "unit", 27),))
    assert (await costs(db_session, source, last.command_id)).total_amount == Decimal("9.00")
    assert await remaining(db_session, source) == 0


async def test_intact_blister_damage_smaller_than_dispense_step_is_not_a_sale(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session, step=6)
    await dispense(db_session, source, (cart_line(source, "unit", 6),))
    pack_id = (await packs(db_session, source))[0][0]
    result = await post(db_session, source, selection(pack_id=pack_id, blisters=((2, 1),)))
    assert result["base_units"] == "1"
    current = (await packs(db_session, source))[0]
    assert current[1]["intact_blisters"] == [3]
    assert current[1]["opened_blisters"] == [
        {"number": 1, "remaining_units": 4},
        {"number": 2, "remaining_units": 9},
    ]


@pytest.mark.parametrize("blister", [None, 10])
async def test_full_opened_writeoff_exhausts_only_the_selected_real_pack(
    db_session: AsyncSession,
    blister: int | None,
) -> None:
    source = await priced_source(db_session, quantity=2, blister=blister)
    await dispense(db_session, source, (cart_line(source, "unit", 1),))
    pack_id = (await packs(db_session, source))[0][0]
    selected = (
        selection(pack_id=pack_id, loose=29)
        if blister is None
        else selection(pack_id=pack_id, blisters=((1, 9), (2, 10), (3, 10)))
    )
    result = await post(db_session, source, selected)
    assert result["base_units"] == "29" and result["closed_packs"] == "0"
    assert await remaining(db_session, source) == 30
    current = (await packs(db_session, source))[0]
    assert current[0] == pack_id and current[2] == 0
    assert current[1] == {"intact_blisters": [], "opened_blisters": [], "loose_units": 0}


@pytest.mark.parametrize("failure", ["sealed", "fragment", "missing pack", "wrong composition"])
async def test_invalid_selection_rolls_back_the_entire_operation(
    db_session: AsyncSession,
    failure: str,
) -> None:
    source = await priced_source(db_session, quantity=2)
    await dispense(db_session, source, (cart_line(source, "unit", 1),))
    pack_id = (await packs(db_session, source))[0][0]
    selected = {
        "sealed": selection(closed=2),
        "fragment": selection(closed=1, pack_id=pack_id, blisters=((1, 10),)),
        "missing pack": selection(closed=1, pack_id=uuid4(), blisters=((1, 1),)),
        "wrong composition": selection(closed=1, pack_id=pack_id, loose=1),
    }[failure]
    operation_id = uuid4()
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, source, selected, operation_id=operation_id)
    assert await status(db_session, operation_id) is None
    assert await facts(db_session) == before


@pytest.mark.parametrize("changed", ["selection", "reason", "comment"])
async def test_reused_operation_id_cannot_reinterpret_the_original_writeoff(
    db_session: AsyncSession,
    changed: str,
) -> None:
    source = await priced_source(db_session, quantity=2)
    operation_id = uuid4()
    result = await post(db_session, source, selection(closed=1), operation_id=operation_id)
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(
                db_session,
                source,
                selection(closed=2 if changed == "selection" else 1),
                operation_id=operation_id,
                reason="other" if changed == "reason" else "damaged",
                comment="Changed" if changed == "comment" else None,
            )
    assert await status(db_session, operation_id) == result
    assert await facts(db_session) == before


async def test_outer_rollback_restores_stock_and_permits_clean_same_id_retry(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session)
    operation_id = uuid4()
    before = await facts(db_session)
    savepoint = await db_session.begin_nested()
    await post(db_session, source, selection(closed=1), operation_id=operation_id)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await savepoint.rollback()
    assert await facts(db_session) == before
    assert await status(db_session, operation_id) is None
    result = await post(db_session, source, selection(closed=1), operation_id=operation_id)
    assert await status(db_session, operation_id) == result
    assert await remaining(db_session, source) == 0


async def test_writeoff_of_transferred_opened_stock_preserves_its_original_basis(
    db_session: AsyncSession,
) -> None:
    legacy = await partial_source(db_session)
    initialized = await post_transition(db_session, legacy.batch_id, legacy.record_id, uuid4())
    source = await physical_source(db_session, initialized["lot_id"])
    opening_before = (await all_facts(db_session))["unit_stock_opening_pack_v2"]
    result = await post(db_session, source, selection(pack_id=legacy.pack_ids[0], loose=1))
    assert result["base_units"] == "1" and await remaining(db_session, source) == 44
    rows = await report(db_session, datetime.now(UTC) + timedelta(seconds=1))
    assert len(rows) == 1 and rows[0]["value"] == Decimal("14.67")
    taken = await dispense(db_session, source, (cart_line(source, "unit", 14),))
    assert (await costs(db_session, source, taken.command_id)).total_amount == Decimal("4.67")
    assert (await all_facts(db_session))["unit_stock_opening_pack_v2"] == opening_before
    assert await remaining(db_session, source) == 30


async def test_blocked_lot_rejects_writeoff_without_claiming_an_operation(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session)
    await set_blocked(db_session, source, True)
    operation_id = uuid4()
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(db_session, source, selection(closed=1), operation_id=operation_id)
    assert await status(db_session, operation_id) is None
    assert await facts(db_session) == before


async def test_expired_stock_can_be_written_off_without_rewriting_accepted_history(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    incoming = await seed_v2_incoming(db_session)
    await db_session.execute(
        text(
            "INSERT INTO tenant_settings(tenant_id,report_timezone) VALUES(:tenant,'Etc/GMT+12') "
            "ON CONFLICT(tenant_id) DO UPDATE SET report_timezone=EXCLUDED.report_timezone"
        ),
        {"tenant": context.stock.tenant_id},
    )
    await db_session.execute(
        text(
            "UPDATE incoming_item SET expires_at="
            "(statement_timestamp() AT TIME ZONE 'Etc/GMT+12')::date+1 WHERE id=:id"
        ),
        {"id": incoming.item_ids[0]},
    )
    await accept_v2(db_session, incoming)
    row = (
        await db_session.execute(
            text("SELECT id,packaging FROM unit_stock_lot_v2 WHERE source_item_id=:id"),
            {"id": incoming.item_ids[0]},
        )
    ).one()
    source = PhysicalSource(incoming, row.id, UnitPackagingV2.model_validate(row.packaging))
    await db_session.execute(
        text("UPDATE tenant_settings SET report_timezone='Etc/GMT-14' WHERE tenant_id=:tenant"),
        {"tenant": context.stock.tenant_id},
    )
    expired = await db_session.scalar(
        text(
            "SELECT expires_at<=(statement_timestamp() AT TIME ZONE 'Etc/GMT-14')::date "
            "FROM unit_stock_lot_v2 WHERE id=:id"
        ),
        {"id": source.lot_id},
    )
    assert expired is True
    before = await facts(db_session)
    result = await post(db_session, source, selection(closed=1), reason="expired")
    assert result["reason"] == "expired" and result["base_units"] == "30"
    assert await remaining(db_session, source) == 30
    after = await facts(db_session)
    assert after["incoming_document"] == before["incoming_document"]
    assert after["incoming_item"] == before["incoming_item"]


@pytest.mark.parametrize("missing", PERMISSIONS)
async def test_real_database_permissions_are_required_for_post_and_recovery(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    missing: str,
) -> None:
    context = runtime(db_session)
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=tuple(code for code in PERMISSIONS if code != missing),
    )
    source = await priced_source(db_session)
    operation_id = uuid4()
    before = await facts(db_session)
    await set_receiving_context(db_session, context.stock, actor)
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await post(
                db_session, source, selection(closed=1), operation_id=operation_id, actor_id=actor
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await status(db_session, operation_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await status(db_session, operation_id) is None
    assert await facts(db_session) == before


@pytest.mark.parametrize("forged", ["actor", "tenant", "branch"])
async def test_sql_arguments_cannot_override_authenticated_scope(
    db_session: AsyncSession,
    forged: str,
) -> None:
    source = await priced_source(db_session)
    operation_id = uuid4()
    arguments = {f"{forged}_id": uuid4()}
    before = await facts(db_session)
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await post(
                db_session, source, selection(closed=1), operation_id=operation_id, **arguments
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    assert await status(db_session, operation_id) is None
    assert await facts(db_session) == before


async def test_authorized_other_actor_cannot_recover_or_replay_someone_elses_operation(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=PERMISSIONS
    )
    source = await priced_source(db_session)
    operation_id = uuid4()
    await post(db_session, source, selection(closed=1), operation_id=operation_id)
    before = await facts(db_session)
    await set_receiving_context(db_session, context.stock, actor)
    assert await status(db_session, operation_id) is None
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await post(
                db_session, source, selection(closed=1), operation_id=operation_id, actor_id=actor
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


async def test_foreign_tenant_context_cannot_recover_existing_writeoff(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session)
    operation_id = uuid4()
    await post(db_session, source, selection(closed=1), operation_id=operation_id)
    before = await facts(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await status(db_session, operation_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("action", ["SELECT", "INSERT", "UPDATE", "DELETE"])
async def test_app_cannot_bypass_writer_or_read_private_writeoff_cost_history(
    db_session: AsyncSession,
    table: str,
    action: str,
) -> None:
    statements = {
        "SELECT": f"SELECT * FROM {table}",
        "INSERT": f"INSERT INTO {table} DEFAULT VALUES",
        "UPDATE": f"UPDATE {table} SET created_at=clock_timestamp()",
        "DELETE": f"DELETE FROM {table}",
    }
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(text(statements[action]))
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    metadata = (
        await db_session.execute(
            text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:table"),
            {"table": table},
        )
    ).one()
    assert tuple(metadata) == (True, True)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
async def test_owner_cannot_rewrite_private_writeoff_journal(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    table: str,
    action: str,
) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session)
    operation_id = uuid4()
    result = await post(db_session, source, selection(closed=1), operation_id=operation_id)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    identity = "id" if table == "physical_writeoff_command" else "operation_id"
    predicate = f"WHERE tenant_id=:tenant AND {identity}=:operation"
    statement = (
        f"UPDATE {table} SET created_at=clock_timestamp() {predicate}"
        if action == "UPDATE"
        else f"DELETE FROM {table} {predicate}"
    )
    with pytest.raises(DBAPIError) as denied:
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text(statement), {"tenant": context.stock.tenant_id, "operation": operation_id}
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "55000"
    assert await status(db_session, operation_id) == result
    assert await remaining(db_session, source) == 0


async def test_concurrent_exact_replay_posts_only_one_writeoff(db_session: AsyncSession) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session, quantity=2)
    operation_id = uuid4()
    await db_session.commit()

    async def attempt() -> dict[str, object]:
        async with context.factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            return await post(session, source, selection(closed=1), operation_id=operation_id)

    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), timeout=15)
    assert results[0] == results[1]
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await status(db_session, operation_id) == results[0]
    assert await remaining(db_session, source) == 30


async def test_raw_audit_snapshots_never_persist_writeoff_cost_fields(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session)
    await post(db_session, source, selection(closed=1))
    rows = (
        await db_session.execute(
            text(
                "SELECT table_name,new_values FROM audit_log WHERE tenant_id=:tenant "
                "AND table_name IN ('physical_writeoff_command','physical_writeoff_entry')"
            ),
            {"tenant": runtime(db_session).stock.tenant_id},
        )
    ).all()
    assert {row.table_name for row in rows} == set(TABLES)
    for row in rows:
        assert not {"total_cost", "cost_amount", "cost_original_pack_cost"}.intersection(
            row.new_values
        )


async def test_http_replay_and_readonly_recovery_expose_only_safe_saved_result(
    client: AsyncClient,
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session, quantity=2)
    payload = request(source)
    async with writeoff_actor(db_session):
        response = await client.post(PATH, json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert_no_cost_fields(result)
        assert response.headers["cache-control"] == "private, no-store"
        assert result["operation_id"] == payload["operation_id"]
        before = await facts(db_session)
        replay = await client.post(PATH, json=payload)
        assert replay.status_code == 200 and replay.json() == result
        assert await facts(db_session) == before
        await db_session.commit()
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE tenant SET status='readonly' WHERE id=:id"),
                {"id": context.stock.tenant_id},
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)
        before = await facts(db_session)
        recovered = await client.get(
            f"{PATH}/operations/{payload['operation_id']}",
            params={"branch_id": payload["branch_id"]},
        )
        assert recovered.status_code == 200 and recovered.json() == result
        assert recovered.headers["cache-control"] == "private, no-store"
        denied = await client.post(PATH, json=payload)
        assert denied.status_code == 422, denied.text
        assert await facts(db_session) == before


@pytest.mark.parametrize("wrong", ["branch", "tenant", "actor", "disjoint scopes"])
async def test_http_claims_cannot_override_database_identity_or_permission_intersection(
    client: AsyncClient,
    db_session: AsyncSession,
    wrong: str,
) -> None:
    source = await priced_source(db_session)
    payload = request(source)
    before = await facts(db_session)
    async with writeoff_actor(db_session) as actor:
        if wrong == "branch":
            payload["branch_id"] = str(uuid4())
        elif wrong == "tenant":
            actor.tenant_id = uuid4()
        elif wrong == "actor":
            actor.user_id = uuid4()
        else:
            actor.permission_scopes = {
                "batches.view": frozenset({source.incoming.stock.branch_id}),
                "batches.write_off": frozenset({uuid4()}),
            }
        response = await denied_http(client, db_session, "POST", PATH, payload=payload)
        assert response.status_code == 403, response.text
    assert await facts(db_session) == before


@pytest.mark.parametrize("missing", PERMISSIONS)
async def test_revoked_live_permission_denies_original_actor_replay_and_recovery(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    missing: str,
) -> None:
    context = runtime(db_session)
    reduced = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=tuple(code for code in PERMISSIONS if code != missing),
    )
    source = await priced_source(db_session, quantity=2)
    payload = request(source)
    async with writeoff_actor(db_session):
        response = await client.post(PATH, json=payload)
        assert response.status_code == 200, response.text
        await db_session.commit()
        async with db_engine.begin() as support:
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            await support.execute(
                text(
                    "UPDATE user_assignment SET role_id=(SELECT role_id FROM user_assignment "
                    "WHERE user_id=:reduced AND tenant_id=:tenant AND branch_id=:branch) "
                    "WHERE user_id=:actor AND tenant_id=:tenant AND branch_id=:branch"
                ),
                {
                    "reduced": reduced,
                    "tenant": context.stock.tenant_id,
                    "branch": context.stock.branch_id,
                    "actor": context.actor_id,
                },
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)
        before = await remaining(db_session, source) if missing != "batches.view" else None
        replay = await denied_http(client, db_session, "POST", PATH, payload=payload)
        read = await denied_http(
            client,
            db_session,
            "GET",
            f"{PATH}/operations/{payload['operation_id']}",
            branch_id=payload["branch_id"],
        )
        assert replay.status_code == read.status_code == 403
        if before is not None:
            assert await remaining(db_session, source) == before


@pytest.mark.parametrize("blister", [None, 10])
async def test_database_projection_matches_the_pure_planner_for_actual_opened_stock(
    db_session: AsyncSession,
    blister: int | None,
) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session, quantity=2, blister=blister)
    await dispense(db_session, source, (cart_line(source, "unit", 1),))
    lot_rows = await db_session.execute(
        text("SELECT * FROM unit_stock_lot_v2 WHERE id=:id"), {"id": source.lot_id}
    )
    lot = lot_rows.mappings().one()
    rows = (
        await db_session.execute(
            text("SELECT id,opened_at,state FROM unit_stock_opened_pack_v2 WHERE lot_id=:id"),
            {"id": source.lot_id},
        )
    ).all()
    before = UnitStockBatchV2(
        id=source.lot_id,
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        catalog_id=context.stock.catalog_id,
        packaging=source.packaging,
        expires_at=lot["expires_at"],
        created_at=lot["created_at"],
        closed_packs=lot["closed_packs"],
        opened_packs=tuple(OpenedPackV2(id=r.id, opened_at=r.opened_at, **r.state) for r in rows),
        is_blocked=lot["is_blocked"],
    )
    pack_id = before.opened_packs[0].id
    selected = (
        selection(closed=1, pack_id=pack_id, loose=3)
        if blister is None
        else selection(closed=1, pack_id=pack_id, blisters=((1, 3), (2, 2)))
    )
    expected = plan_physical_writeoff_v2(
        batch=before,
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        selection=PhysicalWriteOffSelectionV2.model_validate(selected),
    )
    result = await post(db_session, source, selected)
    assert result["base_units"] == str(expected.base_units)
    projected = await packs(db_session, source)
    assert [(row[0], row[1]) for row in projected] == [
        (pack.id, pack.model_dump(mode="json", exclude={"id", "opened_at"}))
        for pack in expected.after.opened_packs
    ]
    assert await db_session.scalar(
        text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE id=:id"), {"id": source.lot_id}
    ) == expected.after.closed_packs


async def test_concurrent_sale_and_writeoff_cannot_both_consume_the_last_unit(
    db_session: AsyncSession,
) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session, blister=None)
    await dispense(db_session, source, (cart_line(source, "unit", 29),))
    pack_id = (await packs(db_session, source))[0][0]
    operation_id = uuid4()
    await db_session.commit()

    async def attempt(writeoff: bool) -> bool:
        try:
            async with context.factory.begin() as session:
                session.info["receiving_runtime"] = context
                await set_receiving_context(session, context.stock, context.actor_id)
                if writeoff:
                    await post(
                        session,
                        source,
                        selection(pack_id=pack_id, loose=1),
                        operation_id=operation_id,
                    )
                else:
                    await dispense(session, source, (cart_line(source, "unit", 1),))
                return True
        except (BusinessRuleError, DBAPIError):
            return False

    results = await asyncio.wait_for(asyncio.gather(attempt(True), attempt(False)), timeout=15)
    assert sum(results) == 1
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await remaining(db_session, source) == 0
    assert (await status(db_session, operation_id) is not None) == results[0]
