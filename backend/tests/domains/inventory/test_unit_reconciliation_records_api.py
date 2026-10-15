"""Saved observations are immutable history, never a stock conversion."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.inventory.repository import InventoryRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_physical_inventory_api import inventory_actor
from tests.domains.inventory.test_unit_legacy_reconciliation_api import body, facts, legacy_batch
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def path(batch_id: UUID) -> str:
    return f"/api/v1/batches/{batch_id}/unit-reconciliation/records"


def request_body(session: AsyncSession) -> dict[str, object]:
    return {
        **body(session),
        "reconciliation_id": str(uuid4()),
        "expected_legacy_quantity": "0.999",
    }


async def stock_facts(session: AsyncSession) -> dict[str, list[object]]:
    snapshot = await facts(session)
    snapshot.pop("audit_log")
    return snapshot


async def increase_quantity(session: AsyncSession, batch_id: UUID) -> None:
    await InventoryRepository(session).insert_movement(
        tenant_id=runtime(session).stock.tenant_id,
        batch_id=batch_id,
        movement_type="correction",
        qty_delta=Decimal("0.001"),
    )


async def test_record_replay_and_read_preserve_stock_and_original_exact_observation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch = await legacy_batch(db_session)
    request = request_body(db_session)
    before = await stock_facts(db_session)
    async with inventory_actor(db_session):
        saved = await client.post(path(batch.id), json=request)
        assert saved.status_code == 200, saved.text
        payload = saved.json()
        assert payload["id"] == request["reconciliation_id"]
        assert payload["created_by"] == str(runtime(db_session).actor_id)
        assert payload["created_at"]
        assert payload["preview"]["source"]["legacy_quantity"] == "0.999"
        assert payload["preview"]["recorded_base_units"] == "29.970"
        assert payload["preview"]["discrepancy_base_units"] == "0.030"
        assert_no_cost_fields(payload)
        assert "purchase_price" not in saved.text
        assert saved.headers["cache-control"] == "private, no-store"
        await db_session.commit()
        context = runtime(db_session)
        await set_receiving_context(db_session, context.stock, context.actor_id)
        after_save = await facts(db_session)
        replay = await client.post(path(batch.id), json=request)
        read = await client.get(
            f"{path(batch.id)}/{payload['id']}", params={"branch_id": str(batch.branch_id)}
        )
        assert replay.status_code == read.status_code == 200
        assert replay.json() == read.json() == payload
        assert read.headers["cache-control"] == "private, no-store"
        assert await facts(db_session) == after_save
    assert await stock_facts(db_session) == before


@pytest.mark.parametrize(
    "field,value", [("closed_packs", 2), ("expected_legacy_quantity", "1.000")]
)
async def test_same_uuid_with_changed_request_is_conflict(
    client: AsyncClient, db_session: AsyncSession, field: str, value: object
) -> None:
    batch = await legacy_batch(db_session)
    request = request_body(db_session)
    async with inventory_actor(db_session):
        saved = await client.post(path(batch.id), json=request)
        assert saved.status_code == 200, saved.text
        before = await facts(db_session)
        conflict = await client.post(path(batch.id), json={**request, field: value})
        assert conflict.status_code == 409
        assert await facts(db_session) == before


async def test_saved_replay_survives_changed_stock_but_new_stale_request_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch = await legacy_batch(db_session)
    request = request_body(db_session)
    async with inventory_actor(db_session):
        saved = await client.post(path(batch.id), json=request)
        assert saved.status_code == 200, saved.text
        await increase_quantity(db_session, batch.id)
        before = await facts(db_session)
        replay = await client.post(path(batch.id), json=request)
        assert replay.status_code == 200 and replay.json() == saved.json()
        stale = await client.post(
            path(batch.id), json={**request, "reconciliation_id": str(uuid4())}
        )
        assert stale.status_code == 409
        assert await facts(db_session) == before


@pytest.mark.parametrize("permission", ["batches.view", "batches.update", "pos.sell"])
async def test_record_creation_requires_both_view_and_update(
    client: AsyncClient, db_session: AsyncSession, permission: str
) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session) as actor:
        actor.permissions = {permission}
        actor.permission_scopes = {permission: frozenset({batch.branch_id})}
        response = await client.post(path(batch.id), json=request_body(db_session))
    assert response.status_code == 403


async def test_view_only_can_read_saved_document_but_wrong_scope_cannot(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session) as actor:
        saved = await client.post(path(batch.id), json=request_body(db_session))
        assert saved.status_code == 200, saved.text
        endpoint = f"{path(batch.id)}/{saved.json()['id']}"
        params = {"branch_id": str(batch.branch_id)}
        actor.permissions = {"batches.view"}
        assert (await client.get(endpoint, params=params)).json() == saved.json()
        actor.permission_scopes["batches.view"] = frozenset({uuid4()})
        assert (await client.get(endpoint, params=params)).status_code == 403
        actor.permission_scopes["batches.view"] = None
        assert (await client.get(endpoint, params={"branch_id": str(uuid4())})).status_code == 404
        actor.tenant_id = uuid4()
        assert (await client.get(endpoint, params=params)).status_code == 404


@pytest.mark.parametrize("field", ["created_by", "preview", "source", "legacy_quantity"])
async def test_client_cannot_forge_saved_server_fields(
    client: AsyncClient, db_session: AsyncSession, field: str
) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session):
        response = await client.post(
            path(batch.id), json={**request_body(db_session), field: "forged"}
        )
    assert response.status_code == 422


async def test_another_actor_can_read_but_cannot_replay_someone_elses_command(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    batch = await legacy_batch(db_session)
    request = request_body(db_session)
    async with inventory_actor(db_session) as actor:
        saved = await client.post(path(batch.id), json=request)
        assert saved.status_code == 200, saved.text
        await set_receiving_context(db_session, context.stock, other)
        actor.user_id = other
        replay = await client.post(path(batch.id), json=request)
        assert replay.status_code == 409
        read = await client.get(
            f"{path(batch.id)}/{saved.json()['id']}", params={"branch_id": str(batch.branch_id)}
        )
        assert read.status_code == 200 and read.json() == saved.json()


async def test_readonly_tenant_keeps_document_reads_but_cannot_save_new_records(
    client: AsyncClient, db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    batch = await legacy_batch(db_session)
    context = runtime(db_session)
    async with inventory_actor(db_session):
        saved = await client.post(path(batch.id), json=request_body(db_session))
        assert saved.status_code == 200, saved.text
        await db_session.commit()
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)
        before = await facts(db_session)
        denied = await client.post(path(batch.id), json=request_body(db_session))
        assert denied.status_code == 422
        read = await client.get(
            f"{path(batch.id)}/{saved.json()['id']}", params={"branch_id": str(batch.branch_id)}
        )
        assert read.status_code == 200 and read.json() == saved.json()
        assert await facts(db_session) == before


@pytest.mark.parametrize("known_blisters,observed", [(False, 15), (True, 24)])
async def test_saved_opened_pack_preserves_physical_composition_on_read(
    client: AsyncClient, db_session: AsyncSession, known_blisters: bool, observed: int
) -> None:
    batch = await legacy_batch(db_session)
    pack = {
        "id": str(uuid4()),
        "opened_at": "2026-10-15T12:00:00Z",
        "intact_blisters": [3, 1] if known_blisters else [],
        "opened_blisters": [{"number": 2, "remaining_units": 4}] if known_blisters else [],
        "loose_units": 0 if known_blisters else 15,
    }
    request = {
        **request_body(db_session),
        "packaging": {
            "version": 2,
            "unit": "tablet",
            "units_per_pack": 30,
            "units_per_blister": 10 if known_blisters else None,
            "dispense_step": 1,
        },
        "closed_packs": 0,
        "opened_packs": [pack],
    }
    before = await stock_facts(db_session)
    async with inventory_actor(db_session):
        saved = await client.post(path(batch.id), json=request)
        assert saved.status_code == 200, saved.text
        payload = saved.json()
        assert payload["preview"]["observed_base_units"] == observed
        assert payload["preview"]["observation"]["opened_packs"][0] == pack
        read = await client.get(
            f"{path(batch.id)}/{payload['id']}", params={"branch_id": str(batch.branch_id)}
        )
        assert read.status_code == 200 and read.json() == payload
    assert await stock_facts(db_session) == before


async def test_saved_replay_rechecks_revoked_update_permission_from_database(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    viewer = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("batches.view",)
    )
    batch = await legacy_batch(db_session)
    request = request_body(db_session)
    async with inventory_actor(db_session):
        saved = await client.post(path(batch.id), json=request)
        assert saved.status_code == 200, saved.text
        await db_session.commit()
        async with db_engine.begin() as support:
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            await support.execute(
                text(
                    "UPDATE user_assignment SET role_id=(SELECT role_id FROM user_assignment "
                    "WHERE user_id=:viewer AND tenant_id=:tenant AND branch_id=:branch) "
                    "WHERE user_id=:actor AND tenant_id=:tenant AND branch_id=:branch"
                ),
                {
                    "viewer": viewer,
                    "actor": context.actor_id,
                    "tenant": context.stock.tenant_id,
                    "branch": context.stock.branch_id,
                },
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert (
            await db_session.scalar(
                text("SELECT tenant_actor_has_scoped_permission(:tenant,'batches.update',:branch)"),
                {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id},
            )
            is False
        )
        before = await facts(db_session)
        denied = await client.post(path(batch.id), json=request)
        assert denied.status_code == 403
        read = await client.get(
            f"{path(batch.id)}/{saved.json()['id']}", params={"branch_id": str(batch.branch_id)}
        )
        assert read.status_code == 200 and read.json() == saved.json()
        assert await facts(db_session) == before
