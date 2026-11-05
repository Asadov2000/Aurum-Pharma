"""Public draft preparation runs under real app RLS without financial posting."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.main import app
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_physical_cart import physical_draft, put_physical
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


@asynccontextmanager
async def cart_actor(session: AsyncSession) -> AsyncIterator[CurrentUser]:
    context = runtime(session)
    actor = CurrentUser(
        user_id=context.actor_id,
        tenant_id=context.stock.tenant_id,
        is_developer=False,
        is_administrator=False,
        permissions={"pos.sell"},
        permission_scopes={"pos.sell": frozenset({context.stock.branch_id})},
    )

    async def override_db() -> AsyncIterator[AsyncSession]:
        yield session

    async def override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = override_user
    try:
        yield actor
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)


def item_body(session: AsyncSession) -> dict[str, object]:
    return {
        "catalog_id": str(runtime(session).stock.catalog_id),
        "position": 1,
        "sale_format": "blister",
        "quantity": 2,
        "discount_amount": "0.01",
    }


async def test_cart_crud_quote_replay_and_no_financial_effects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    sale = await physical_draft(db_session)
    path = f"/api/v1/sales/{sale.id}/physical-cart"
    item_id = uuid4()
    async with cart_actor(db_session) as actor:
        assert (await client.get(path)).json() == []
        empty_quote = await client.get(f"{path}/quote")
        assert empty_quote.status_code == 422
        body = item_body(db_session)
        created = await client.put(f"{path}/items/{item_id}", json=body)
        assert created.status_code == 200
        assert created.json()["physical_packaging"]["units_per_blister"] == 10
        assert (await client.put(f"{path}/items/{item_id}", json=body)).json() == created.json()
        listed = await client.get(path)
        assert listed.status_code == 200 and listed.json() == [created.json()]
        assert listed.headers["cache-control"] == "private, no-store"
        first_quote = await client.get(f"{path}/quote")
        assert first_quote.status_code == 200
        assert first_quote.headers["cache-control"] == "private, no-store"
        assert first_quote.json()["quote"]["total_amount"] == "19.99"
        assert first_quote.json()["quote"]["lines"][0]["base_units"] == 20
        body["quantity"] = 3
        assert (await client.put(f"{path}/items/{item_id}", json=body)).status_code == 200
        changed_quote = await client.get(f"{path}/quote")
        assert changed_quote.json()["revision_hash"] != first_quote.json()["revision_hash"]
        assert changed_quote.json()["quote"]["total_amount"] == "29.99"
        # Tenant-wide capability still resolves this draft's real branch, never all branches.
        actor.permission_scopes["pos.sell"] = None
        assert (await client.get(path)).status_code == 200
        removed = await client.delete(f"{path}/items/{item_id}")
        assert removed.status_code == 204 and removed.content == b""
        assert (await client.get(path)).json() == []
    assert await db_session.scalar(
        text("SELECT total_amount FROM sale WHERE id=:id"), {"id": sale.id}
    ) == Decimal("0.00")
    for table in ("sale_payment", "pos_payment_attempt", "unit_stock_movement_v2"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": runtime(db_session).stock.tenant_id},
            )
            == 0
        )


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": 1.5},
        {"quantity": "2"},
        {"quantity": True},
        {"quantity": 0},
        {"position": 201},
        {"sale_format": "tablet"},
        {"discount_amount": 0.5},
        {"tenant_id": str(uuid4())},
        {"unit_price": "0.01"},
        {"physical_packaging": {}},
    ],
)
async def test_cart_api_rejects_untrusted_fields_and_nonwhole_quantity(
    client: AsyncClient, db_session: AsyncSession, change: dict[str, object]
) -> None:
    sale = await physical_draft(db_session)
    path = f"/api/v1/sales/{sale.id}/physical-cart"
    async with cart_actor(db_session):
        response = await client.put(f"{path}/items/{uuid4()}", json=item_body(db_session) | change)
        assert response.status_code == 422
        assert (await client.get(path)).json() == []


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_all_cart_endpoints_enforce_actor_and_paired_scope(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id)
    path = f"/api/v1/sales/{sale.id}/physical-cart"
    async with cart_actor(db_session) as actor:
        if denial == "permission":
            actor.permissions.clear()
        elif denial == "branch":
            actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        elif denial == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        assert (await client.get(path)).status_code == 403
        assert (await client.get(f"{path}/quote")).status_code == 403
        assert (
            await client.put(f"{path}/items/{item.id}", json=item_body(db_session))
        ).status_code == 403
        assert (await client.delete(f"{path}/items/{item.id}")).status_code == 403


async def test_cart_quote_preserves_legacy_rows_separately(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    sale = await physical_draft(db_session)
    await add_legacy(db_session, sale)
    await put_physical(db_session, sale.id, sale_format="pack")
    async with cart_actor(db_session):
        response = await client.get(f"/api/v1/sales/{sale.id}/physical-cart/quote")
        assert response.status_code == 200
        lines = response.json()["quote"]["lines"]
        assert [row["inventory_kind"] for row in lines] == ["legacy_pack", "physical_unit"]
        assert lines[1]["sale_format"] == "pack" and lines[1]["base_units"] == 30


async def test_cart_api_closed_shift_blocks_even_reading(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    sale = await physical_draft(db_session)
    await db_session.execute(
        text("UPDATE shift SET status='closed',closed_at=now() WHERE id=:id"), {"id": sale.shift_id}
    )
    async with cart_actor(db_session):
        assert (await client.get(f"/api/v1/sales/{sale.id}/physical-cart")).status_code == 422


@pytest.mark.parametrize("other_tenant", [False, True])
async def test_real_other_cashier_and_rls_tenant_cannot_read_or_change_cart(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    other_tenant: bool,
) -> None:
    context = runtime(db_session)
    stock = context.stock
    if other_tenant:
        async with AsyncSession(db_engine) as support:
            async with support.begin():
                await support.execute(text("SELECT set_config('app.support_session','true',true)"))
                stock = await seed_ledger_context(support)
    other_actor = await seed_receiving_actor(db_engine, maintenance_engine, stock)
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id)
    path = f"/api/v1/sales/{sale.id}/physical-cart"
    await set_receiving_context(db_session, stock, other_actor)
    async with cart_actor(db_session) as actor:
        actor.user_id = other_actor
        actor.tenant_id = stock.tenant_id
        actor.permission_scopes["pos.sell"] = None
        expected = 404 if other_tenant else 403
        assert (await client.get(path)).status_code == expected
        assert (await client.get(f"{path}/quote")).status_code == expected
        assert (
            await client.put(f"{path}/items/{item.id}", json=item_body(db_session))
        ).status_code == expected
        assert (await client.delete(f"{path}/items/{item.id}")).status_code == expected


async def test_readonly_tenant_can_read_but_not_mutate_cart(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    sale = await physical_draft(db_session)
    item = await put_physical(db_session, sale.id)
    await db_session.commit()
    context = runtime(db_session)
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    path = f"/api/v1/sales/{sale.id}/physical-cart"
    async with cart_actor(db_session):
        assert (await client.get(path)).status_code == 200
        assert (await client.get(f"{path}/quote")).status_code == 200
        assert (
            await client.put(f"{path}/items/{item.id}", json=item_body(db_session))
        ).status_code == 422
        assert (await client.delete(f"{path}/items/{item.id}")).status_code == 422


async def test_support_session_cannot_prepare_cashier_cart(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    sale = await physical_draft(db_session)
    context = runtime(db_session)
    async with AsyncSession(db_engine) as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "SELECT set_config('app.tenant_id',:tenant,true), "
                "set_config('app.user_id',:actor,true)"
            ),
            {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
        )
        assert await support.scalar(text("SELECT session_user")) == "aurum_support"

        async def override_support_db() -> AsyncIterator[AsyncSession]:
            yield support

        async with cart_actor(db_session):
            app.dependency_overrides[get_db] = override_support_db
            assert (await client.get(f"/api/v1/sales/{sale.id}/physical-cart")).status_code == 403
