"""Unfinished unit drafts keep their shift editable until cleared or completed."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor, item_body
from tests.domains.pos.test_physical_cash_checkout import facts
from tests.domains.pos.test_physical_cash_checkout_api import cash_path, quoted_request
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def allow_close(actor: CurrentUser, branch_id: UUID) -> None:
    actor.permissions.add("pos.shift_close")
    actor.permission_scopes["pos.shift_close"] = frozenset({branch_id})


async def add_physical(client: AsyncClient, session: AsyncSession, sale_id: UUID) -> UUID:
    item_id = uuid4()
    response = await client.put(
        f"/api/v1/sales/{sale_id}/physical-cart/items/{item_id}", json=item_body(session)
    )
    assert response.status_code == 200
    return item_id


async def assert_open(session: AsyncSession, shift_id: UUID) -> None:
    row = (
        await session.execute(
            text("SELECT status,closed_at,closed_by_user_id FROM shift WHERE id=:id"),
            {"id": shift_id},
        )
    ).one()
    assert tuple(row) == ("open", None, None)


@pytest.mark.parametrize("saved_intent", [False, True])
async def test_physical_draft_blocks_close_without_stock_or_money_effects(
    client: AsyncClient, db_session: AsyncSession, saved_intent: bool
) -> None:
    source = await checkout_source(db_session)
    sale_id, shift_id = source.sale.id, source.sale.shift_id
    context = runtime(db_session)
    async with cart_actor(db_session) as actor:
        allow_close(actor, context.stock.branch_id)
        await add_physical(client, db_session, sale_id)
        if saved_intent:
            request = await quoted_request(client, sale_id)
            response = await client.post(
                f"{cash_path(sale_id)}/intent", json=request.model_dump(mode="json")
            )
            assert response.status_code == 200 and response.json()["state"] == "saved_draft"
        before = await facts(db_session)
        response = await client.post(
            f"/api/v1/shifts/{shift_id}/close", json={"closing_cash_actual": "0.00"}
        )
        assert response.status_code == 422
        assert await facts(db_session) == before
        await assert_open(db_session, shift_id)
        cart = await client.get(f"/api/v1/sales/{sale_id}/physical-cart")
        assert cart.status_code == 200 and len(cart.json()) == 1


async def test_mixed_draft_still_blocks_close_after_legacy_row_is_removed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    sale_id, shift_id = source.sale.id, source.sale.shift_id
    legacy = await add_legacy(db_session, source.sale)
    async with cart_actor(db_session) as actor:
        allow_close(actor, runtime(db_session).stock.branch_id)
        await add_physical(client, db_session, sale_id)
        response = await client.request(
            "DELETE",
            f"/api/v1/sales/{sale_id}/items/{legacy.id}",
            json={"operation_id": str(uuid4())},
        )
        assert response.status_code == 200
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM sale_item WHERE sale_id=:id"), {"id": sale_id}
            )
            == 0
        )
        before = await facts(db_session)
        response = await client.post(
            f"/api/v1/shifts/{shift_id}/close", json={"closing_cash_actual": "0.00"}
        )
        assert response.status_code == 422
        assert await facts(db_session) == before
        await assert_open(db_session, shift_id)


async def test_removing_last_physical_row_allows_shift_close(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    sale_id, shift_id = source.sale.id, source.sale.shift_id
    async with cart_actor(db_session) as actor:
        allow_close(actor, runtime(db_session).stock.branch_id)
        item_id = await add_physical(client, db_session, sale_id)
        path = f"/api/v1/sales/{sale_id}/physical-cart"
        before = await facts(db_session)
        assert (await client.delete(f"{path}/items/{item_id}")).status_code == 204
        assert (await client.get(path)).json() == []
        after_delete = await facts(db_session)
        for table in ("unit_stock_lot_v2", "unit_stock_movement_v2", "sale_payment"):
            assert after_delete[table] == before[table]
        response = await client.post(
            f"/api/v1/shifts/{shift_id}/close", json={"closing_cash_actual": "0.00"}
        )
        assert response.status_code == 200 and response.json()["status"] == "closed"
        assert response.json()["closing_cash_expected"] == "0.00"


async def test_completed_physical_sale_does_not_block_shift_close(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    sale_id, shift_id = source.sale.id, source.sale.shift_id
    async with cart_actor(db_session) as actor:
        allow_close(actor, runtime(db_session).stock.branch_id)
        await add_physical(client, db_session, sale_id)
        request = await quoted_request(client, sale_id)
        body = request.model_dump(mode="json")
        path = cash_path(sale_id)
        assert (await client.post(f"{path}/intent", json=body)).status_code == 200
        assert (await client.post(f"{path}/complete", json=body)).status_code == 200
        before = await facts(db_session)
        response = await client.post(
            f"/api/v1/shifts/{shift_id}/close",
            json={"closing_cash_actual": str(request.expected_total_amount)},
        )
        assert response.status_code == 200 and response.json()["status"] == "closed"
        assert Decimal(response.json()["closing_cash_expected"]) == request.expected_total_amount
        after = await facts(db_session)
        for table in ("unit_stock_lot_v2", "unit_stock_movement_v2", "sale_payment", "sale"):
            assert after[table] == before[table]


async def test_manager_cannot_close_shift_whose_physical_rows_are_hidden_by_rls(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    # Seed the second assignment before domain calls take access-revision locks.
    manager_id = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=("pos.shift_close", "pos.manage_shifts"),
    )
    source = await checkout_source(db_session)
    sale_id, shift_id = source.sale.id, source.sale.shift_id
    async with cart_actor(db_session) as actor:
        await add_physical(client, db_session, sale_id)
        before = await facts(db_session)
        await set_receiving_context(db_session, context.stock, manager_id)
        actor.user_id = manager_id
        actor.permissions = {"pos.shift_close", "pos.manage_shifts"}
        actor.permission_scopes = {
            permission: frozenset({context.stock.branch_id}) for permission in actor.permissions
        }
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM pos_unit_cart_item WHERE sale_id=:id"), {"id": sale_id}
            )
            == 0
        )
        savepoint = await db_session.begin_nested()
        try:
            response = await client.post(
                f"/api/v1/shifts/{shift_id}/close", json={"closing_cash_actual": "0.00"}
            )
            assert response.status_code == 422
        finally:
            await savepoint.rollback()
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert await facts(db_session) == before
        await assert_open(db_session, shift_id)
