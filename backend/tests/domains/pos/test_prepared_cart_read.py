"""Sale detail reads restore draft intentions without editable-cart locks."""

from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_unit_cart import put_cart_item
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_physical_cart import physical_draft, put_physical
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


@pytest.mark.parametrize("kind", ["empty", "physical", "v1", "mixed"])
async def test_sale_details_restore_prepared_rows_after_shift_closes(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    sale = await physical_draft(db_session)
    expected_ids: list[str] = []
    if kind == "v1":
        expected_ids.append(str(await put_cart_item(db_session, sale.id, quantity=3)))
    elif kind in {"physical", "mixed"}:
        if kind == "mixed":
            await add_legacy(db_session, sale)
        for position, sale_format in enumerate(("unit", "blister", "pack"), 2):
            item = await put_physical(
                db_session,
                sale.id,
                position=position,
                sale_format=sale_format,
                quantity=2,
                discount=Decimal("0.01"),
            )
            expected_ids.append(str(item.id))
    await db_session.execute(
        text("UPDATE shift SET status='closed',closed_at=now() WHERE id=:id"),
        {"id": sale.shift_id},
    )
    async with cart_actor(db_session) as actor:
        actor.permissions.add("sales.view.own")
        actor.permission_scopes["sales.view.own"] = frozenset({sale.branch_id})
        response = await client.get(f"/api/v1/sales/{sale.id}")
        assert response.status_code == 200
        details = response.json()
        rows = details["prepared_cart_items"]
        assert [row["id"] for row in rows] == expected_ids
        assert len(details["items"]) == (1 if kind == "mixed" else 0)
        assert details["payments"] == []
        assert all(row["sale_id"] == str(sale.id) for row in rows)
        assert all(row["tenant_id"] == str(sale.tenant_id) for row in rows)
        if kind == "v1":
            assert "physical_packaging" not in rows[0]
            assert rows[0]["quantity"] == 3
        elif kind in {"physical", "mixed"}:
            assert [row["sale_format"] for row in rows[:3]] == ["unit", "blister", "pack"]
            assert all(row["physical_packaging"]["units_per_blister"] == 10 for row in rows[:3])
            assert all(
                row["quantity"] == 2 and row["discount_amount"] == "0.01" for row in rows[:3]
            )
        # Existing financial and legacy item fields retain their original meaning.
        assert details["total_amount"] == ("0.02" if kind == "mixed" else "0.00")


@pytest.mark.parametrize("denial", ["cashier", "branch", "tenant"])
async def test_prepared_rows_do_not_weaken_sale_read_authorization(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    denial: str,
) -> None:
    context = runtime(db_session)
    actor_id = context.actor_id
    stock = context.stock
    if denial == "tenant":
        async with AsyncSession(db_engine) as support:
            async with support.begin():
                await support.execute(text("SELECT set_config('app.support_session','true',true)"))
                stock = await seed_ledger_context(support)
    if denial != "branch":
        actor_id = await seed_receiving_actor(db_engine, maintenance_engine, stock)
    sale = await physical_draft(db_session)
    await put_physical(db_session, sale.id, quantity=2)
    if denial != "branch":
        db_session.expunge(sale)
        await set_receiving_context(db_session, stock, actor_id)
    async with cart_actor(db_session) as actor:
        actor.user_id = actor_id
        actor.tenant_id = stock.tenant_id
        actor.permissions.add("sales.view.own")
        actor.permission_scopes["sales.view.own"] = frozenset(
            {uuid4() if denial == "branch" else stock.branch_id}
        )
        response = await client.get(f"/api/v1/sales/{sale.id}")
        assert response.status_code == (404 if denial == "tenant" else 403)
        assert "prepared_cart_items" not in response.json()


async def test_manager_sale_read_does_not_claim_another_cashiers_prepared_rows(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    manager = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    sale = await physical_draft(db_session)
    await put_physical(db_session, sale.id)
    db_session.expunge(sale)
    await set_receiving_context(db_session, context.stock, manager)
    async with cart_actor(db_session) as actor:
        actor.user_id = manager
        actor.permissions.add("sales.view.tenant")
        actor.permission_scopes["sales.view.tenant"] = None
        response = await client.get(f"/api/v1/sales/{sale.id}")
        assert response.status_code == 200
        assert response.json()["prepared_cart_items"] == []
