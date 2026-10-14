"""Physical inventory HTTP boundaries use actual app roles and immutable quantities."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    cart_line,
    dispense,
    receive_source,
    remaining,
)
from tests.domains.inventory.test_unit_physical_blocking import facts, state
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
PATH = "/api/v1/batches/physical"


@asynccontextmanager
async def inventory_actor(session: AsyncSession) -> AsyncIterator[CurrentUser]:
    async with cart_actor(session) as actor:
        actor.permissions = {"batches.view", "batches.update"}
        actor.permission_scopes = {
            code: frozenset({runtime(session).stock.branch_id}) for code in actor.permissions
        }
        yield actor


def body(session: AsyncSession, blocked: bool, revision: str) -> dict[str, object]:
    return {
        "branch_id": str(runtime(session).stock.branch_id),
        "is_blocked": blocked,
        "expected_blocking_revision": revision,
    }


async def test_list_closed_opened_and_block_revision_without_stock_effects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 3),))
    params = {"branch_id": str(source.incoming.stock.branch_id)}
    path = f"{PATH}/{source.lot_id}/blocking"
    async with inventory_actor(db_session):
        listed = await client.get(PATH, params=params)
        assert listed.status_code == 200, listed.text
        assert listed.headers["cache-control"] == "private, no-store"
        row = listed.json()["items"][0]
        assert (row["closed_packs"], row["opened_units"], row["base_units"]) == ("1", "27", "57")
        assert row["packaging"] == source.packaging.model_dump(mode="json")
        assert row["catalog_name"] and listed.json()["total"] == 1
        assert_no_cost_fields(listed.json())
        blocked = await client.patch(path, json=body(db_session, True, "0"))
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["blocking_revision"] == "1"
        before = await facts(db_session, source)
        noop = await client.patch(path, json=body(db_session, True, "1"))
        assert noop.json() == blocked.json()
        stale = await client.patch(path, json=body(db_session, False, "0"))
        assert stale.status_code == 409
        assert await facts(db_session, source) == before
        assert await remaining(db_session, source) == 57
        unblocked = await client.patch(path, json=body(db_session, False, "1"))
        assert unblocked.status_code == 200
        assert unblocked.json()["blocking_revision"] == "2"
        assert await remaining(db_session, source) == 57


async def test_filters_empty_lots_pagination_and_literal_search(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await receive_source(db_session)
    params = {"branch_id": str(source.incoming.stock.branch_id)}
    async with inventory_actor(db_session):
        for extra in ({"q": "%"}, {"catalog_id": str(uuid4())}, {"is_blocked": "true"}):
            result = await client.get(PATH, params={**params, **extra})
            assert result.status_code == 200 and result.json()["items"] == []
        found = await client.get(PATH, params={**params, "q": str(source.lot_id)[:8]})
        assert found.json()["total"] == 1
        page = await client.get(PATH, params={**params, "page": 2, "page_size": 1})
        assert page.json()["total"] == 1 and page.json()["items"] == []
        await dispense(db_session, source, (cart_line(source, "pack", 2),))
        assert (await client.get(PATH, params=params)).json()["total"] == 0
        empty = await client.get(PATH, params={**params, "show_empty": "true"})
        assert empty.json()["items"][0]["base_units"] == "0"


@pytest.mark.parametrize("permissions", [("batches.view",), ("pos.sell",), ("batches.update",)])
async def test_real_view_only_or_pos_actor_does_not_inherit_http_claims(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    permissions: tuple[str, ...],
) -> None:
    source = await receive_source(db_session)
    original = runtime(db_session)
    await db_session.commit()
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, original.stock, permissions=permissions
    )
    db_session.info["receiving_runtime"] = replace(original, actor_id=actor_id)
    await set_receiving_context(db_session, original.stock, actor_id)
    async with inventory_actor(db_session):
        listed = await client.get(PATH, params={"branch_id": str(original.stock.branch_id)})
        assert listed.status_code == (200 if permissions == ("batches.view",) else 403)
        if listed.status_code == 200:
            assert listed.json()["items"][0]["catalog_name"]
            assert_no_cost_fields(listed.json())
        denied = await client.patch(
            f"{PATH}/{source.lot_id}/blocking", json=body(db_session, False, "0")
        )
        assert denied.status_code == 403


async def test_http_scope_and_missing_lot_are_denied(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await receive_source(db_session)
    async with inventory_actor(db_session) as actor:
        denied = await client.get(PATH, params={"branch_id": str(uuid4())})
        assert denied.status_code == 403
        missing = await client.patch(
            f"{PATH}/{uuid4()}/blocking", json=body(db_session, True, "0")
        )
        assert missing.status_code == 404
        actor.permissions = {"batches.view"}
        denied = await client.patch(
            f"{PATH}/{source.lot_id}/blocking", json=body(db_session, False, "0")
        )
        assert denied.status_code == 403


@pytest.mark.parametrize("value", [1, "true", None])
async def test_mutation_rejects_non_boolean_and_extra_fields(
    client: AsyncClient, db_session: AsyncSession, value: object
) -> None:
    source = await receive_source(db_session)
    async with inventory_actor(db_session):
        invalid = {**body(db_session, True, "0"), "is_blocked": value}
        response = await client.patch(f"{PATH}/{source.lot_id}/blocking", json=invalid)
        assert response.status_code == 422
        extra = {**body(db_session, True, "0"), "closed_packs": 0}
        response = await client.patch(f"{PATH}/{source.lot_id}/blocking", json=extra)
        assert response.status_code == 422
    assert await state(db_session, source) == (False, 0)


@pytest.mark.parametrize("status", ["readonly", "suspended", "archived"])
async def test_tenant_readonly_denies_even_noop_and_preserves_reads(
    client: AsyncClient,
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    status: str,
) -> None:
    source = await receive_source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE tenant SET status=:status WHERE id=:tenant"),
            {"status": status, "tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with inventory_actor(db_session):
        for blocked in (False, True):
            denied = await client.patch(
                f"{PATH}/{source.lot_id}/blocking", json=body(db_session, blocked, "0")
            )
            assert denied.status_code == 422
        listed = await client.get(PATH, params={"branch_id": str(context.stock.branch_id)})
        assert listed.status_code == 200
    assert await state(db_session, source) == (False, 0)


@pytest.mark.parametrize("mode,modules", [("readonly", []), ("full", ["inventory"])])
async def test_policy_denies_noop_without_changing_lot(
    client: AsyncClient,
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    mode: str,
    modules: list[str],
) -> None:
    source = await receive_source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
        await owner.execute(
            text("ALTER TABLE tenant_access_policy DISABLE TRIGGER a00_guard_tenant_access_policy")
        )
        await owner.execute(
            text(
                "UPDATE tenant_access_policy SET version=version+1,mode=:mode, "
                "blocked_modules=:modules "
                "WHERE tenant_id=:tenant"
            ),
            {"mode": mode, "modules": modules, "tenant": context.stock.tenant_id},
        )
        await owner.execute(
            text("ALTER TABLE tenant_access_policy ENABLE TRIGGER a00_guard_tenant_access_policy")
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with inventory_actor(db_session):
        for blocked in (False, True):
            denied = await client.patch(
                f"{PATH}/{source.lot_id}/blocking", json=body(db_session, blocked, "0")
            )
            assert denied.status_code == 403
    assert await state(db_session, source) == (False, 0)


@pytest.mark.parametrize("changed", ["branch", "tenant", "actor"])
async def test_actual_database_identity_rejects_overstated_http_context(
    client: AsyncClient, db_session: AsyncSession, changed: str
) -> None:
    source = await receive_source(db_session)
    branch_id = source.incoming.stock.branch_id
    async with inventory_actor(db_session) as actor:
        if changed == "branch":
            branch_id = uuid4()
            actor.permission_scopes = {code: frozenset({branch_id}) for code in actor.permissions}
        elif changed == "tenant":
            actor.tenant_id = uuid4()
        else:
            actor.user_id = uuid4()
        listed = await client.get(PATH, params={"branch_id": str(branch_id)})
        assert listed.status_code == 403
        denied = await client.patch(
            f"{PATH}/{source.lot_id}/blocking",
            json={**body(db_session, False, "0"), "branch_id": str(branch_id)},
        )
        assert denied.status_code == 403
    assert await state(db_session, source) == (False, 0)


async def test_actual_inactive_actor_cannot_read_or_replay_noop(
    client: AsyncClient, db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    source = await receive_source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE app_user SET status='blocked' WHERE id=:id"), {"id": context.actor_id}
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with inventory_actor(db_session):
        listed = await client.get(PATH, params={"branch_id": str(context.stock.branch_id)})
        assert listed.status_code == 403
        denied = await client.patch(
            f"{PATH}/{source.lot_id}/blocking", json=body(db_session, False, "0")
        )
        assert denied.status_code == 403
