"""Physical format previews expose scoped stock facts without posting or reserving."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import get_db
from app.main import app
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense
from tests.domains.inventory.test_unit_physical_blocking import set_blocked
from tests.domains.pos.test_physical_cart_api import cart_actor, db_session
from tests.domains.pos.test_unit_physical_cart import physical_draft
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, facts
from tests.domains.pos.test_unit_return import runtime

__all__ = ["db_session"]


async def test_options_all_formats_prices_and_physical_stock_without_side_effects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    context = runtime(db_session)
    before = await facts(db_session)
    async with cart_actor(db_session):
        response = await client.get(
            f"/api/v1/sales/{source.sale.id}/physical-cart/options/{context.stock.catalog_id}"
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        body = response.json()
        assert body["state"] == "physical_options_preview"
        assert body["tenant_id"] == str(context.stock.tenant_id)
        assert body["branch_id"] == str(context.stock.branch_id)
        assert body["sale_id"] == str(source.sale.id)
        assert body["catalog_id"] == str(context.stock.catalog_id)
        assert body["currency"] == "TJS"
        assert body["checkout_enabled"] is False and body["stock_reserved"] is False
        assert body["stock"] == {"base_units": 60, "closed_packs": 2, "intact_blisters": 6}
        assert body["packaging"]["units_per_pack"] == 30
        assert body["packaging"]["units_per_blister"] == 10
        assert body["options"] == [
            {
                "sale_format": sale_format,
                "configured": True,
                "quantity_step": 1,
                "retail_unit_price": price,
                "unavailable_reason": None,
            }
            for sale_format, price in (("pack", "60.00"), ("blister", "20.00"), ("unit", "2.00"))
        ]
    assert await facts(db_session) == before


async def test_options_count_opened_physical_packs_and_ignore_legacy_stock(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    await dispense(db_session, source.stock, (cart_line(source.stock, "unit", 1),))
    context = runtime(db_session)
    before = await facts(db_session)
    async with cart_actor(db_session):
        response = await client.get(
            f"/api/v1/sales/{source.sale.id}/physical-cart/options/{context.stock.catalog_id}"
        )
        assert response.status_code == 200
        assert response.json()["stock"] == {
            "base_units": 59,
            "closed_packs": 1,
            "intact_blisters": 5,
        }
    assert await facts(db_session) == before


@pytest.mark.parametrize("excluded", ["blocked", "expired", "packaging"])
async def test_options_exclude_ineligible_physical_lots(
    client: AsyncClient,
    db_session: AsyncSession,
    excluded: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await checkout_source(db_session)
    context = runtime(db_session)
    if excluded == "blocked":
        await set_blocked(db_session, source.stock, True)
    elif excluded == "expired":
        # Advance only the preview clock; received lot provenance stays immutable.
        later = datetime.now(UTC) + timedelta(days=365)
        monkeypatch.setattr("app.domains.pos.physical_cart_options_service.utc_now", lambda: later)
    else:
        await db_session.execute(
            text(
                "UPDATE tenant_catalog SET unit_sale_preparation="
                "jsonb_set(unit_sale_preparation,'{units_per_blister}','5') WHERE id=:id"
            ),
            {"id": context.stock.catalog_id},
        )
    async with cart_actor(db_session):
        response = await client.get(
            f"/api/v1/sales/{source.sale.id}/physical-cart/options/{context.stock.catalog_id}"
        )
        assert response.status_code == 200
        assert response.json()["stock"] == {
            "base_units": 0,
            "closed_packs": 0,
            "intact_blisters": 0,
        }
        assert all(option["configured"] for option in response.json()["options"])


@pytest.mark.parametrize("preparation", ["missing", "unknown_blister", "step"])
async def test_options_explain_preparation_and_expose_valid_quantity_steps(
    client: AsyncClient, db_session: AsyncSession, preparation: str
) -> None:
    sale = await physical_draft(db_session)
    context = runtime(db_session)
    updates = {
        "missing": "NULL",
        "unknown_blister": "unit_sale_preparation - 'units_per_blister'",
        "step": "jsonb_set(unit_sale_preparation,'{dispense_step}','6')",
    }
    await db_session.execute(
        text(
            f"UPDATE tenant_catalog SET unit_sale_preparation={updates[preparation]} WHERE id=:id"
        ),
        {"id": context.stock.catalog_id},
    )
    async with cart_actor(db_session):
        response = await client.get(
            f"/api/v1/sales/{sale.id}/physical-cart/options/{context.stock.catalog_id}"
        )
        assert response.status_code == 200
        body = response.json()
        if preparation == "missing":
            assert body["packaging"] is None
            assert all(option["unavailable_reason"] == "not_prepared" for option in body["options"])
        elif preparation == "unknown_blister":
            assert body["stock"]["intact_blisters"] is None
            assert body["options"][1] == {
                "sale_format": "blister",
                "configured": False,
                "quantity_step": None,
                "retail_unit_price": None,
                "unavailable_reason": "unknown_blister",
            }
            assert body["options"][0]["configured"] and body["options"][2]["configured"]
        else:
            assert [option["quantity_step"] for option in body["options"]] == [1, 3, 6]
            assert all(option["configured"] for option in body["options"])


async def test_options_missing_pack_price_preserves_explicit_unit_and_blister_prices(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    context = runtime(db_session)
    await db_session.execute(
        text("UPDATE tenant_catalog SET base_price=NULL WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    before = await facts(db_session)
    async with cart_actor(db_session):
        response = await client.get(
            f"/api/v1/sales/{source.sale.id}/physical-cart/options/{context.stock.catalog_id}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["stock"]["base_units"] == 60
        assert body["options"][0] == {
            "sale_format": "pack",
            "configured": False,
            "quantity_step": 1,
            "retail_unit_price": None,
            "unavailable_reason": "price_unavailable",
        }
        assert body["options"][1]["configured"] is True
        assert body["options"][1]["retail_unit_price"] == "20.00"
        assert body["options"][2]["configured"] is True
        assert body["options"][2]["retail_unit_price"] == "2.00"
    assert await facts(db_session) == before


@pytest.mark.parametrize("restriction", ["inactive", "special"])
async def test_options_do_not_expose_prices_or_stock_for_unavailable_catalog(
    client: AsyncClient, db_session: AsyncSession, restriction: str
) -> None:
    source = await checkout_source(db_session)
    context = runtime(db_session)
    update = "is_active=false" if restriction == "inactive" else "dispensing_type='special'"
    await db_session.execute(
        text(f"UPDATE tenant_catalog SET {update} WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    async with cart_actor(db_session):
        response = await client.get(
            f"/api/v1/sales/{source.sale.id}/physical-cart/options/{context.stock.catalog_id}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["packaging"] is None
        assert body["stock"] == {"base_units": 0, "closed_packs": 0, "intact_blisters": None}
        assert all(
            option["unavailable_reason"] == "catalog_unavailable" for option in body["options"]
        )
        assert all(option["retail_unit_price"] is None for option in body["options"])


@pytest.mark.parametrize("denial", ["permission", "branch"])
async def test_options_require_selling_permission_in_draft_branch(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    sale = await physical_draft(db_session)
    context = runtime(db_session)
    async with cart_actor(db_session) as actor:
        if denial == "permission":
            actor.permissions.clear()
        else:
            actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        response = await client.get(
            f"/api/v1/sales/{sale.id}/physical-cart/options/{context.stock.catalog_id}"
        )
        assert response.status_code == 403


@pytest.mark.parametrize("other_tenant", [False, True])
async def test_options_real_other_cashier_and_tenant_are_denied(
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
    await set_receiving_context(db_session, stock, other_actor)
    async with cart_actor(db_session) as actor:
        actor.user_id = other_actor
        actor.tenant_id = stock.tenant_id
        actor.permission_scopes["pos.sell"] = None
        response = await client.get(
            f"/api/v1/sales/{sale.id}/physical-cart/options/{context.stock.catalog_id}"
        )
        assert response.status_code == (404 if other_tenant else 403)


async def test_options_unknown_catalog_is_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    sale = await physical_draft(db_session)
    async with cart_actor(db_session):
        response = await client.get(f"/api/v1/sales/{sale.id}/physical-cart/options/{uuid4()}")
        assert response.status_code == 404


async def test_support_session_cannot_read_cashier_options(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    sale = await physical_draft(db_session)
    context = runtime(db_session)
    async with AsyncSession(db_engine) as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "SELECT set_config('app.tenant_id',:tenant,true),"
                "set_config('app.user_id',:actor,true)"
            ),
            {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
        )
        assert await support.scalar(text("SELECT session_user")) == "aurum_support"

        async def override_support_db() -> AsyncIterator[AsyncSession]:
            yield support

        async with cart_actor(db_session):
            app.dependency_overrides[get_db] = override_support_db
            response = await client.get(
                f"/api/v1/sales/{sale.id}/physical-cart/options/{context.stock.catalog_id}"
            )
            assert response.status_code == 403
