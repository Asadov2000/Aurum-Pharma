"""Binding recovery is uncached, scoped, and available without write access."""

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_unit_payment_intent import intent_request, save
from tests.domains.pos.test_unit_payment_plan import snapshot
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def test_binding_http_lifecycle_unknown_and_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    path = (
        f"/api/v1/sales/{request.request.sale_id}/unit-payment-plan/intents/{request.operation_id}"
    )
    before = await snapshot(db_session)
    async with cart_actor(db_session) as actor:
        assert (await client.get(f"{path}/binding")).status_code == 404
        bound = await client.post(f"{path}/bind")
        assert bound.status_code == 200, bound.text
        assert bound.json()["state"] == "bound"
        assert (await client.post(f"{path}/bind")).json() == bound.json()
        recovered = await client.get(f"{path}/binding")
        assert recovered.json() == bound.json()
        assert recovered.headers["cache-control"] == "private, no-store"
        unknown = path.replace(str(request.operation_id), str(uuid4()))
        for suffix in ("bind", "cancel-binding"):
            assert (await client.post(f"{unknown}/{suffix}")).status_code == 404
        assert (await client.get(f"{unknown}/binding")).status_code == 404
        actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        for suffix in ("bind", "cancel-binding"):
            assert (await client.post(f"{path}/{suffix}")).status_code == 403
        assert (await client.get(f"{path}/binding")).status_code == 403
        actor.permission_scopes["pos.sell"] = frozenset({runtime(db_session).stock.branch_id})
        cancelled = await client.post(f"{path}/cancel-binding")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["state"] == "cancelled"
        assert (await client.get(f"{path}/binding")).json() == cancelled.json()
        assert (await client.post(f"{path}/bind")).status_code == 409
    assert await snapshot(db_session) == before


async def test_readonly_tenant_can_read_binding_but_cannot_bind_or_cancel(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    path = (
        f"/api/v1/sales/{request.request.sale_id}/unit-payment-plan/intents/{request.operation_id}"
    )
    async with cart_actor(db_session):
        bound = await client.post(f"{path}/bind")
        assert bound.status_code == 200, bound.text
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
        recovered = await client.get(f"{path}/binding")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == bound.json()
        for suffix in ("bind", "cancel-binding"):
            assert (await client.post(f"{path}/{suffix}")).status_code == 422
    assert await snapshot(db_session) == before
