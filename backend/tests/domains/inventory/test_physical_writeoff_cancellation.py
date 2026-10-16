"""Cancellation fences late warehouse requests and never reverses posted losses."""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.inventory.test_physical_writeoff_posting import (
    PATH,
    PERMISSIONS,
    denied_http,
    facts,
    post,
    request,
    selection,
    status,
    writeoff_actor,
)
from tests.domains.inventory.test_unit_cost_ledger_v2 import priced_source
from tests.domains.inventory.test_unit_ledger_v2_dispensing import PhysicalSource, remaining
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def cancel(
    session: AsyncSession,
    source: PhysicalSource,
    operation_id: UUID,
    *,
    reason: str = "damaged",
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
    branch_id: UUID | None = None,
) -> dict[str, object]:
    context = runtime(session)
    result = await session.scalar(
        text(
            "SELECT public.cancel_physical_writeoff(:tenant,:branch,:operation,:actor,:lot,"
            ":reason,NULL,CAST(:selection AS jsonb))"
        ),
        {
            "tenant": tenant_id or context.stock.tenant_id,
            "branch": branch_id or source.incoming.stock.branch_id,
            "operation": operation_id,
            "actor": actor_id or context.actor_id,
            "lot": source.lot_id,
            "reason": reason,
            "selection": json.dumps(selection(closed=1)),
        },
    )
    assert isinstance(result, dict)
    return result


async def test_cancel_is_terminal_idempotent_and_does_not_change_stock_or_cost(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session, quantity=2)
    before = await facts(db_session)
    operation_id = uuid4()
    result = await cancel(db_session, source, operation_id)
    assert result["status"] == "cancelled"
    assert result["selection"] == selection(closed=1)
    assert_no_cost_fields(result)
    after = await facts(db_session)
    assert after == before | {"audit_log": after["audit_log"]}
    assert len(after["audit_log"]) == len(before["audit_log"]) + 1
    added = [row for row in after["audit_log"] if row not in before["audit_log"]]
    assert len(added) == 1
    audit = added[0]
    assert isinstance(audit, dict)
    assert audit["table_name"] == "physical_writeoff_cancellation"
    assert audit["action"] == "INSERT"
    assert audit["new_values"]["id"] == str(operation_id)
    assert await cancel(db_session, source, operation_id) == result
    assert await status(db_session, operation_id) == result
    assert await facts(db_session) == after
    async with db_session.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            await post(db_session, source, selection(closed=1), operation_id=operation_id)
        assert getattr(error.value.orig, "sqlstate", None) == "23505"
        await savepoint.rollback()
    assert await facts(db_session) == after


async def test_cancel_after_post_returns_original_result_without_reversal(
    db_session: AsyncSession,
) -> None:
    source = await priced_source(db_session, quantity=2)
    operation_id = uuid4()
    result = await post(db_session, source, selection(closed=1), operation_id=operation_id)
    before = await facts(db_session)
    assert await cancel(db_session, source, operation_id) == result
    assert await facts(db_session) == before
    assert await remaining(db_session, source) == 30


@pytest.mark.parametrize("posted", [False, True])
async def test_cancel_rejects_changed_command(db_session: AsyncSession, posted: bool) -> None:
    source = await priced_source(db_session)
    operation_id = uuid4()
    if posted:
        await post(db_session, source, selection(closed=1), operation_id=operation_id)
    else:
        await cancel(db_session, source, operation_id)
    async with db_session.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            await cancel(db_session, source, operation_id, reason="other")
        assert getattr(error.value.orig, "sqlstate", None) == "23505"
        await savepoint.rollback()


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_serialized_concurrent_post_cancel_has_one_terminal_result(
    db_session: AsyncSession, cancel_first: bool
) -> None:
    source = await priced_source(db_session, quantity=2)
    context = runtime(db_session)
    operation_id = uuid4()
    await db_session.commit()
    started = asyncio.Event()
    release = asyncio.Event()

    async def first() -> dict[str, object]:
        async with context.factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            result = (
                await cancel(session, source, operation_id)
                if cancel_first
                else await post(session, source, selection(closed=1), operation_id=operation_id)
            )
            started.set()
            await release.wait()
            return result

    async def second() -> dict[str, object] | str:
        await started.wait()
        try:
            async with context.factory.begin() as session:
                session.info["receiving_runtime"] = context
                await set_receiving_context(session, context.stock, context.actor_id)
                if cancel_first:
                    return await post(
                        session, source, selection(closed=1), operation_id=operation_id
                    )
                return await cancel(session, source, operation_id)
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "23505"
            return "cancelled"

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        # The first transaction deliberately retains the common operation lock.
        await asyncio.sleep(0.05)
        assert not second_task.done()
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=15)
    assert results[1] == ("cancelled" if cancel_first else results[0])
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await status(db_session, operation_id) == results[0]
    assert await remaining(db_session, source) == (60 if cancel_first else 30)


async def test_http_cancel_lost_response_recovers_and_fences_late_post(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    source = await priced_source(db_session)
    payload = request(source)
    async with writeoff_actor(db_session):
        response = await client.post(f"{PATH}/cancel", json=payload)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        result = response.json()
        assert result["status"] == "cancelled"
        assert result["comment"] == payload["comment"]
        assert_no_cost_fields(result)
        recovered = await client.get(
            f"{PATH}/operations/{payload['operation_id']}",
            params={"branch_id": payload["branch_id"]},
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == result
        assert (await client.post(f"{PATH}/cancel", json=payload)).json() == result
        late = await denied_http(client, db_session, "POST", PATH, payload=payload)
        assert late.status_code == 409, late.text
        wrong = await client.get(
            f"{PATH}/operations/{payload['operation_id']}", params={"branch_id": str(uuid4())}
        )
        assert wrong.status_code == 403, wrong.text


async def test_cancel_audit_redacts_comment(db_session: AsyncSession, client: AsyncClient) -> None:
    source = await priced_source(db_session)
    async with writeoff_actor(db_session):
        response = await client.post(f"{PATH}/cancel", json=request(source))
        assert response.status_code == 200, response.text
    snapshots = list(
        (
            await db_session.execute(
                text(
                    "SELECT new_values FROM audit_log WHERE tenant_id=:tenant "
                    "AND table_name='physical_writeoff_cancellation'"
                ),
                {"tenant": runtime(db_session).stock.tenant_id},
            )
        ).scalars()
    )
    assert len(snapshots) == 1
    assert "comment" not in snapshots[0]


@pytest.mark.parametrize("forged", ["actor", "tenant", "branch"])
async def test_cancel_arguments_cannot_override_scope(
    db_session: AsyncSession, forged: str
) -> None:
    source = await priced_source(db_session)
    operation_id = uuid4()
    before = await facts(db_session)
    arguments = {f"{forged}_id": uuid4()}
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await cancel(db_session, source, operation_id, **arguments)
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    assert await status(db_session, operation_id) is None
    assert await facts(db_session) == before


@pytest.mark.parametrize("missing", PERMISSIONS)
async def test_cancel_requires_both_real_database_permissions(
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
    await set_receiving_context(db_session, context.stock, actor)
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await cancel(db_session, source, operation_id, actor_id=actor)
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await status(db_session, operation_id) is None


async def test_cancel_history_is_actor_bound(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=PERMISSIONS
    )
    source = await priced_source(db_session)
    operation_id = uuid4()
    await cancel(db_session, source, operation_id)
    await set_receiving_context(db_session, context.stock, actor)
    assert await status(db_session, operation_id) is None
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await cancel(db_session, source, operation_id, actor_id=actor)
    assert getattr(error.value.orig, "sqlstate", None) == "23505"


async def test_cancel_status_remains_readable_after_tenant_becomes_readonly(
    db_session: AsyncSession, client: AsyncClient, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session)
    payload = request(source)
    async with writeoff_actor(db_session):
        response = await client.post(f"{PATH}/cancel", json=payload)
        assert response.status_code == 200, response.text
        await db_session.commit()
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE tenant SET status='readonly' WHERE id=:id"),
                {"id": context.stock.tenant_id},
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)
        recovered = await client.get(
            f"{PATH}/operations/{payload['operation_id']}",
            params={"branch_id": payload["branch_id"]},
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == response.json()
        denied = await client.post(f"{PATH}/cancel", json=payload)
        assert denied.status_code == 422, denied.text


@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
async def test_owner_cannot_rewrite_cancellation_history(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, action: str
) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session)
    operation_id = uuid4()
    result = await cancel(db_session, source, operation_id)
    await db_session.commit()
    predicate = "WHERE tenant_id=:tenant AND id=:operation"
    statement = (
        f"UPDATE physical_writeoff_cancellation SET created_at=clock_timestamp() {predicate}"
        if action == "UPDATE"
        else f"DELETE FROM physical_writeoff_cancellation {predicate}"
    )
    with pytest.raises(DBAPIError) as error:
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text(statement), {"tenant": context.stock.tenant_id, "operation": operation_id}
            )
    assert getattr(error.value.orig, "sqlstate", None) == "55000"
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await status(db_session, operation_id) == result
    assert await remaining(db_session, source) == 30


async def test_cancel_rechecks_actor_after_waiting_for_operation_lock(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    source = await priced_source(db_session)
    operation_id = uuid4()
    await db_session.commit()
    started = asyncio.Event()

    async def attempt() -> None:
        with pytest.raises(DBAPIError) as error:
            async with context.factory.begin() as session:
                session.info["receiving_runtime"] = context
                await set_receiving_context(session, context.stock, context.actor_id)
                started.set()
                await cancel(session, source, operation_id)
        assert getattr(error.value.orig, "sqlstate", None) == "42501"

    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended("
                "'physical-writeoff:'||CAST(:tenant AS text)||':'||CAST(:operation AS text),0))"
            ),
            {"tenant": str(context.stock.tenant_id), "operation": str(operation_id)},
        )
        task = asyncio.create_task(attempt())
        await asyncio.wait_for(started.wait(), timeout=10)
        await asyncio.sleep(0.05)
        assert not task.done()
        await owner.execute(
            text("UPDATE app_user SET status='blocked' WHERE id=:id"), {"id": context.actor_id}
        )
    try:
        await asyncio.wait_for(task, timeout=15)
    finally:
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE app_user SET status='active' WHERE id=:id"),
                {"id": context.actor_id},
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await status(db_session, operation_id) is None
    assert await remaining(db_session, source) == 30
