"""Current comparison preserves saved observations and never authorizes conversion."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_physical_inventory_api import inventory_actor
from tests.domains.inventory.test_unit_legacy_reconciliation_api import facts, legacy_batch
from tests.domains.inventory.test_unit_reconciliation_records_api import (
    increase_quantity,
    path,
    request_body,
)
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def comparison_path(batch_id: UUID, record_id: str) -> str:
    return f"{path(batch_id)}/{record_id}/comparison"


@pytest.mark.parametrize(
    "quantity,closed_packs,issues,recorded,discrepancy",
    [
        ("1.000", 1, [], "30.000", "0.000"),
        ("0.000", 0, [], "0.000", "0.000"),
        ("0.999", 1, ["quantity_not_representable", "count_discrepancy"], "29.970", "0.030"),
        ("1.000", 2, ["count_discrepancy"], "30.000", "30.000"),
    ],
)
async def test_comparison_reads_exact_current_quantity_without_any_writes(
    client: AsyncClient,
    db_session: AsyncSession,
    quantity: str,
    closed_packs: int,
    issues: list[str],
    recorded: str,
    discrepancy: str,
) -> None:
    batch = await legacy_batch(db_session, quantity=quantity)
    request = {
        **request_body(db_session),
        "expected_legacy_quantity": quantity,
        "closed_packs": closed_packs,
    }
    async with inventory_actor(db_session):
        saved = await client.post(path(batch.id), json=request)
        assert saved.status_code == 200, saved.text
        before = await facts(db_session)
        endpoint = comparison_path(batch.id, saved.json()["id"])
        params = {"branch_id": str(batch.branch_id)}
        response = await client.get(endpoint, params=params)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["record"] == saved.json()
        assert payload["issues"] == issues
        assert payload["conversion_authorized"] is False
        current = payload["current_comparison"]
        assert current == saved.json()["preview"]
        assert current["source"]["legacy_quantity"] == quantity
        assert current["recorded_base_units"] == recorded
        assert current["discrepancy_base_units"] == discrepancy
        assert response.headers["cache-control"] == "private, no-store"
        assert_no_cost_fields(payload)
        assert "purchase_price" not in response.text and "sale_price" not in response.text
        repeated = await client.get(endpoint, params=params)
        assert repeated.status_code == 200 and repeated.json() == payload
        assert await facts(db_session) == before


@pytest.mark.parametrize(
    "quantity,expected_issues",
    [
        ("0.999", ["quantity_changed"]),
        ("1.000", ["quantity_changed", "quantity_not_representable", "count_discrepancy"]),
    ],
)
async def test_changed_stock_recomputes_comparison_but_keeps_original_count(
    client: AsyncClient,
    db_session: AsyncSession,
    quantity: str,
    expected_issues: list[str],
) -> None:
    batch = await legacy_batch(db_session, quantity=quantity)
    request = {**request_body(db_session), "expected_legacy_quantity": quantity}
    async with inventory_actor(db_session):
        saved = await client.post(path(batch.id), json=request)
        assert saved.status_code == 200, saved.text
        await increase_quantity(db_session, batch.id)
        before = await facts(db_session)
        params = {"branch_id": str(batch.branch_id)}
        response = await client.get(comparison_path(batch.id, saved.json()["id"]), params=params)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["record"] == saved.json()
        assert payload["issues"] == expected_issues
        assert payload["conversion_authorized"] is False
        current = payload["current_comparison"]
        assert current["observation"] == saved.json()["preview"]["observation"]
        assert current["source"]["legacy_quantity"] == ("1.000" if quantity == "0.999" else "1.001")
        assert current["recorded_base_units"] == ("30.000" if quantity == "0.999" else "30.030")
        assert current["discrepancy_base_units"] == ("0.000" if quantity == "0.999" else "-0.030")
        read = await client.get(f"{path(batch.id)}/{saved.json()['id']}", params=params)
        assert read.status_code == 200 and read.json() == saved.json()
        assert await facts(db_session) == before


@pytest.mark.parametrize("wrong_identity", ["branch", "batch", "record", "tenant", "db_tenant"])
async def test_comparison_does_not_resolve_record_outside_requested_identity(
    client: AsyncClient, db_session: AsyncSession, wrong_identity: str
) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session) as actor:
        saved = await client.post(path(batch.id), json=request_body(db_session))
        assert saved.status_code == 200, saved.text
        before = await facts(db_session)
        batch_id, record_id, branch_id = batch.id, saved.json()["id"], batch.branch_id
        if wrong_identity == "branch":
            branch_id = uuid4()
            actor.permission_scopes["batches.view"] = None
        elif wrong_identity == "batch":
            batch_id = uuid4()
        elif wrong_identity == "record":
            record_id = str(uuid4())
        elif wrong_identity == "db_tenant":
            await db_session.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"),
                {"tenant": str(uuid4())},
            )
        else:
            actor.tenant_id = uuid4()
        response = await client.get(
            comparison_path(batch_id, record_id), params={"branch_id": str(branch_id)}
        )
        assert response.status_code == 404, response.text
        context = runtime(db_session)
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert await facts(db_session) == before


@pytest.mark.parametrize("denial", ["permission", "branch", "tenant"])
async def test_comparison_requires_view_permission_and_tenant_scope(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session) as actor:
        saved = await client.post(path(batch.id), json=request_body(db_session))
        assert saved.status_code == 200, saved.text
        before = await facts(db_session)
        if denial == "permission":
            actor.permissions = {"batches.update"}
        elif denial == "branch":
            actor.permission_scopes["batches.view"] = frozenset({uuid4()})
        else:
            actor.tenant_id = None
        response = await client.get(
            comparison_path(batch.id, saved.json()["id"]),
            params={"branch_id": str(batch.branch_id)},
        )
        assert response.status_code == 403, response.text
        assert await facts(db_session) == before


@pytest.mark.parametrize("readonly", [False, True])
async def test_database_view_only_actor_can_compare_even_in_readonly_tenant(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    readonly: bool,
) -> None:
    context = runtime(db_session)
    viewer = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("batches.view",)
    )
    batch = await legacy_batch(db_session)
    async with inventory_actor(db_session) as actor:
        saved = await client.post(path(batch.id), json=request_body(db_session))
        assert saved.status_code == 200, saved.text
        await db_session.commit()
        if readonly:
            async with maintenance_engine.begin() as owner:
                await owner.execute(
                    text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
                    {"tenant": context.stock.tenant_id},
                )
        await set_receiving_context(db_session, context.stock, viewer)
        actor.user_id = viewer
        actor.permissions = {"batches.view"}
        before = await facts(db_session)
        response = await client.get(
            comparison_path(batch.id, saved.json()["id"]),
            params={"branch_id": str(batch.branch_id)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["record"] == saved.json()
        assert response.json()["conversion_authorized"] is False
        assert response.headers["cache-control"] == "private, no-store"
        assert await facts(db_session) == before
