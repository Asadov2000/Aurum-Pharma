"""Uncached HTTP recovery of saved plans remains available to read-only tenants."""

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_unit_payment_intent import intent_request
from tests.domains.pos.test_unit_payment_plan import snapshot
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def test_intent_http_replay_and_get_are_uncached_scoped_and_read_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request = await intent_request(db_session, mixed=True)
    path = f"/api/v1/sales/{request.request.sale_id}/unit-payment-plan/intents"
    body = request.model_dump(mode="json")
    before = await snapshot(db_session)
    async with cart_actor(db_session) as actor:
        posted = await client.post(path, json=body)
        assert posted.status_code == 200, posted.text
        assert posted.json()["state"] == "saved_draft"
        assert posted.headers["cache-control"] == "private, no-store"
        assert (await client.post(path, json=body)).json() == posted.json()
        recovered = await client.get(f"{path}/{request.operation_id}")
        assert recovered.status_code == 200
        assert recovered.json() == posted.json()
        assert recovered.headers["cache-control"] == "private, no-store"
        assert (await client.get(f"{path}/{uuid4()}")).status_code == 404
        actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        assert (await client.get(f"{path}/{request.operation_id}")).status_code == 403
        assert (await client.post(path, json=body)).status_code == 403
        actor.permission_scopes["pos.sell"] = None
        actor.user_id = uuid4()
        assert (await client.get(f"{path}/{request.operation_id}")).status_code == 403
        assert (await client.post(path, json=body)).status_code == 403
        actor.permissions = set()
        actor.permission_scopes = {}
        assert (await client.get(f"{path}/{request.operation_id}")).status_code == 403
        assert (await client.post(path, json=body)).status_code == 403
    assert await snapshot(db_session) == before


async def test_readonly_tenant_recovers_saved_intent_but_cannot_post(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request = await intent_request(db_session)
    path = f"/api/v1/sales/{request.request.sale_id}/unit-payment-plan/intents"
    body = request.model_dump(mode="json")
    async with cart_actor(db_session):
        posted = await client.post(path, json=body)
        assert posted.status_code == 200, posted.text
    await db_session.commit()
    context = runtime(db_session)
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await snapshot(db_session)
    async with cart_actor(db_session):
        recovered = await client.get(f"{path}/{request.operation_id}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == posted.json()
        assert (await client.post(path, json=body)).status_code == 422
    assert await snapshot(db_session) == before
