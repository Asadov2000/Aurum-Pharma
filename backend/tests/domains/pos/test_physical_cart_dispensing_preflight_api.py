"""Cashiers can read their draft's minimal Rx flag without tenant-wide catalog access."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cart_api import cart_actor, db_session
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_physical_cart import physical_draft, put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import facts
from tests.domains.pos.test_unit_return import runtime

__all__ = ["db_session"]


@pytest.mark.parametrize("kind", ["physical", "legacy", "mixed"])
@pytest.mark.parametrize("prescription", [False, True])
async def test_preflight_requires_only_scoped_pos_sell_and_does_not_change_sale(
    client: AsyncClient, db_session: AsyncSession, kind: str, prescription: bool
) -> None:
    sale = await physical_draft(db_session)
    context = runtime(db_session)
    if kind != "physical":
        await add_legacy(db_session, sale)
    if kind != "legacy":
        await put_physical(db_session, sale.id)
    if prescription:
        await db_session.execute(
            text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
            {"id": context.stock.catalog_id},
        )
    before = await facts(db_session)
    path = f"/api/v1/sales/{sale.id}/physical-cart/dispensing-preflight"
    async with cart_actor(db_session) as actor:
        assert actor.permissions == {"pos.sell"}
        # A catalog.view permission or an Rx handling permission is never needed to read this flag.
        response = await client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        expected = {"sale_id": str(sale.id), "requires_prescription": prescription}
        assert response.json() == expected
        # Tenant-wide pos.sell still resolves this sale, not arbitrary catalog entries.
        actor.permission_scopes["pos.sell"] = None
        assert (await client.get(path)).json() == expected
    assert await facts(db_session) == before


@pytest.mark.parametrize("denial", ["permission", "branch"])
async def test_preflight_requires_selling_permission_in_actual_sale_branch(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    sale = await physical_draft(db_session)
    await put_physical(db_session, sale.id)
    async with cart_actor(db_session) as actor:
        if denial == "permission":
            actor.permissions.clear()
        else:
            actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        response = await client.get(f"/api/v1/sales/{sale.id}/physical-cart/dispensing-preflight")
        assert response.status_code == 403


@pytest.mark.parametrize("restriction", ["inactive", "deleted", "special"])
async def test_preflight_rejects_unavailable_catalog_instead_of_assuming_otc(
    client: AsyncClient, db_session: AsyncSession, restriction: str
) -> None:
    sale = await physical_draft(db_session)
    await put_physical(db_session, sale.id)
    context = runtime(db_session)
    updates = {
        "inactive": "is_active=false",
        "deleted": "deleted_at=now()",
        "special": "dispensing_type='special'",
    }
    await db_session.execute(
        text(f"UPDATE tenant_catalog SET {updates[restriction]} WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    async with cart_actor(db_session):
        response = await client.get(f"/api/v1/sales/{sale.id}/physical-cart/dispensing-preflight")
        assert response.status_code == 422
        assert "requires_prescription" not in response.json()


async def test_preflight_does_not_infer_otc_from_an_empty_or_unknown_sale(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    sale = await physical_draft(db_session)
    async with cart_actor(db_session):
        assert (
            await client.get(f"/api/v1/sales/{sale.id}/physical-cart/dispensing-preflight")
        ).status_code == 422
        assert (
            await client.get(f"/api/v1/sales/{uuid4()}/physical-cart/dispensing-preflight")
        ).status_code == 404


@pytest.mark.parametrize("other_tenant", [False, True])
async def test_preflight_real_other_cashier_and_tenant_are_denied(
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
    await put_physical(db_session, sale.id)
    await set_receiving_context(db_session, stock, other_actor)
    async with cart_actor(db_session) as actor:
        actor.user_id = other_actor
        actor.tenant_id = stock.tenant_id
        actor.permission_scopes["pos.sell"] = None
        response = await client.get(f"/api/v1/sales/{sale.id}/physical-cart/dispensing-preflight")
        assert response.status_code == (404 if other_tenant else 403)
