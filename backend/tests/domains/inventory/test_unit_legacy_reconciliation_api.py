"""Legacy preview HTTP reads scoped server facts without converting inventory."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.inventory.models import Batch
from app.domains.inventory.repository import InventoryRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_physical_inventory_api import inventory_actor
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def path(batch_id: UUID) -> str:
    return f"/api/v1/batches/{batch_id}/unit-reconciliation/preview"


def body(session: AsyncSession, size: int = 30) -> dict[str, object]:
    return {
        "branch_id": str(runtime(session).stock.branch_id),
        "packaging": {
            "version": 2,
            "unit": "tablet",
            "units_per_pack": size,
            "units_per_blister": None,
            "dispense_step": 1,
        },
        "closed_packs": 1,
        "opened_packs": [],
    }


async def legacy_batch(
    session: AsyncSession, *, quantity: str = "0.999", prepared: int | None = None
) -> Batch:
    stock = runtime(session).stock
    preparation = {"version": 1, "unit": "tablet", "units_per_pack": 30, "dispense_step": 1}
    repository = InventoryRepository(session)
    batch = await repository.create_batch(
        tenant_id=stock.tenant_id,
        branch_id=stock.branch_id,
        catalog_id=stock.catalog_id,
        expires_at=date.today() - timedelta(days=1),
        purchase_price=Decimal("7.31"),
        sale_price=Decimal("12.43"),
        qty_initial=Decimal("2.000"),
        qty_remaining=Decimal("0.000"),
        is_blocked=True,
        unit_packaging_preparation=preparation if prepared else None,
        unit_packaging_snapshot_v2=(
            {**preparation, "version": 2, "units_per_blister": 10} if prepared == 2 else None
        ),
    )
    if Decimal(quantity):
        await repository.insert_movement(
            tenant_id=stock.tenant_id,
            batch_id=batch.id,
            movement_type="correction",
            qty_delta=Decimal(quantity),
        )
        await session.refresh(batch)
    return batch


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    return {
        table: list(
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
        for table in ("batch", "batch_movement", "unit_stock_lot_v2", "audit_log")
    }


@pytest.mark.parametrize("quantity,size,recorded", [("0.999", 30, "29.970"), ("0.333", 3, "0.999")])
async def test_preview_uses_current_server_quantity_and_explicit_composition_without_writes(
    client: AsyncClient, db_session: AsyncSession, quantity: str, size: int, recorded: str
) -> None:
    batch = await legacy_batch(db_session, quantity=quantity)
    before = await facts(db_session)
    async with inventory_actor(db_session) as actor:
        actor.permissions = {"batches.view"}
        response = await client.post(path(batch.id), json=body(db_session, size))
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert payload["source"]["legacy_quantity"] == quantity
        assert payload["source"]["batch_id"] == str(batch.id)
        assert payload["source"]["catalog_id"] == str(batch.catalog_id)
        assert payload["recorded_base_units"] == recorded
        assert Decimal(payload["discrepancy_base_units"]) == Decimal(size) - Decimal(recorded)
        assert payload["recorded_quantity_representable"] is False
        assert_no_cost_fields(payload)
        assert "purchase_price" not in response.text and "sale_price" not in response.text
        repeated = await client.post(path(batch.id), json=body(db_session, size))
        assert repeated.json() == payload
    assert await facts(db_session) == before


@pytest.mark.parametrize(
    "field", ["legacy_quantity", "tenant_id", "catalog_id", "source", "operation_id"]
)
async def test_client_cannot_supply_stale_quantity_or_source_identity(
    client: AsyncClient, db_session: AsyncSession, field: str
) -> None:
    batch = await legacy_batch(db_session)
    request = {**body(db_session), field: "1"}
    before = await facts(db_session)
    async with inventory_actor(db_session):
        response = await client.post(path(batch.id), json=request)
    assert response.status_code == 422
    assert await facts(db_session) == before


@pytest.mark.parametrize("permission", ["pos.sell", "batches.update", "batches.view_costs"])
async def test_other_permissions_do_not_grant_preview(
    client: AsyncClient, db_session: AsyncSession, permission: str
) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session) as actor:
        actor.permissions = {permission}
        actor.permission_scopes = {permission: frozenset({batch.branch_id})}
        response = await client.post(path(batch.id), json=body(db_session))
    assert response.status_code == 403


async def test_requested_branch_and_actual_batch_branch_are_both_checked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch = await legacy_batch(db_session)
    other_branch = uuid4()
    request = {**body(db_session), "branch_id": str(other_branch)}
    async with inventory_actor(db_session) as actor:
        denied = await client.post(path(batch.id), json=request)
        assert denied.status_code == 403
        actor.permission_scopes["batches.view"] = None
        missing = await client.post(path(batch.id), json=request)
        assert missing.status_code == 404
        unknown = await client.post(path(uuid4()), json=body(db_session))
        assert unknown.status_code == 404


async def test_another_tenant_cannot_resolve_existing_batch(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session) as actor:
        actor.tenant_id = uuid4()
        response = await client.post(path(batch.id), json=body(db_session))
    assert response.status_code == 404


async def test_missing_tenant_cannot_preview(client: AsyncClient, db_session: AsyncSession) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session) as actor:
        actor.tenant_id = None
        response = await client.post(path(batch.id), json=body(db_session))
    assert response.status_code == 403


@pytest.mark.parametrize("prepared", [1, 2])
async def test_prepared_batches_are_not_treated_as_legacy(
    client: AsyncClient, db_session: AsyncSession, prepared: int
) -> None:
    batch = await legacy_batch(db_session, quantity="1", prepared=prepared)
    before = await facts(db_session)
    async with inventory_actor(db_session):
        response = await client.post(path(batch.id), json=body(db_session))
    assert response.status_code == 409, response.text
    assert await facts(db_session) == before


@pytest.mark.parametrize("count", [True, 1.5, -1, 100_000_000_000])
async def test_invalid_observed_pack_counts_are_rejected(
    client: AsyncClient, db_session: AsyncSession, count: object
) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session):
        response = await client.post(
            path(batch.id), json={**body(db_session), "closed_packs": count}
        )
    assert response.status_code == 422


async def test_opened_pack_identity_survives_preview_and_duplicates_are_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch = await legacy_batch(db_session, quantity="0.500")
    pack = {
        "id": str(uuid4()),
        "opened_at": "2026-10-15T12:00:00Z",
        "intact_blisters": [],
        "opened_blisters": [],
        "loose_units": 15,
    }
    request = {**body(db_session), "closed_packs": 0, "opened_packs": [pack]}
    before = await facts(db_session)
    async with inventory_actor(db_session):
        response = await client.post(path(batch.id), json=request)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["comparison"] == "matched"
        assert payload["observed_base_units"] == 15
        assert payload["observation"]["closed_packs"] == 0
        assert payload["observation"]["opened_packs"][0]["id"] == pack["id"]
        duplicate = await client.post(
            path(batch.id), json={**request, "opened_packs": [pack, pack]}
        )
        assert duplicate.status_code == 422
    assert await facts(db_session) == before


async def test_readonly_tenant_can_preview_exhausted_blocked_expired_batch(
    client: AsyncClient, db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    batch = await legacy_batch(db_session, quantity="0.000")
    context = runtime(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    async with inventory_actor(db_session):
        response = await client.post(path(batch.id), json={**body(db_session), "closed_packs": 0})
    assert response.status_code == 200, response.text
    assert response.json()["comparison"] == "matched"
    assert await facts(db_session) == before
