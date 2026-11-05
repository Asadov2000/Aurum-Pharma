"""Public review preparation and recovery never activate or post an electronic refund."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_unit_electronic_refund_intent import save_review
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def review_path(parent_sale_id: object) -> str:
    return f"/api/v1/sales/{parent_sale_id}/unit-electronic-refunds"


async def refund_workflow_counts(session: AsyncSession) -> dict[str, int]:
    """Separate a saved review from every subsequent execution journal."""
    tenant = runtime(session).stock.tenant_id
    result = {}
    for table in (
        "pos_unit_electronic_refund_intent",
        "pos_unit_electronic_refund_binding",
        "pos_unit_electronic_refund_activation",
        "pos_unit_electronic_refund_outcome",
        "pos_unit_electronic_refund_posting",
    ):
        result[table] = int(
            await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant}
            )
            or 0
        )
    return result


async def test_review_http_quote_save_replay_recovery_leave_financial_facts_unchanged(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request = await save_review(db_session)
    path = review_path(request.request.parent_sale_id)
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": frozenset({runtime(db_session).stock.branch_id})}
        quoted = await client.post(
            f"{path}/quote", json=request.request.selection.model_dump(mode="json")
        )
        assert quoted.status_code == 200, quoted.text
        assert quoted.headers["cache-control"] == "private, no-store"
        assert quoted.json()["state"] == "preview"
        assert quoted.json()["revision_hash"] == request.request.expected_revision_hash
        assert len(quoted.json()["payments"]) == 3
        saved = await client.post(f"{path}/reviews", json=request.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
        assert saved.headers["cache-control"] == "private, no-store"
        assert saved.json()["preview"] == quoted.json()
        assert saved.json()["operation_id"] == str(request.operation_id)
        assert saved.json()["saved_at"]
        replayed = await client.post(f"{path}/reviews", json=request.model_dump(mode="json"))
        assert replayed.status_code == 200, replayed.text
        assert replayed.json() == saved.json()
        recovered = await client.get(f"{path}/reviews/{request.operation_id}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.headers["cache-control"] == "private, no-store"
        assert recovered.json() == saved.json()
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals | {
        "pos_unit_electronic_refund_intent": journals["pos_unit_electronic_refund_intent"] + 1
    }


async def test_review_http_rejects_stale_revision_without_saving(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request = await save_review(db_session)
    stale = request.model_copy(
        update={"request": request.request.model_copy(update={"expected_revision_hash": "0" * 64})}
    )
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        response = await client.post(
            f"{review_path(request.request.parent_sale_id)}/reviews",
            json=stale.model_dump(mode="json"),
        )
        assert response.status_code == 409, response.text
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals


async def test_review_http_binds_body_to_url_and_recovery_to_parent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request = await save_review(db_session)
    path = review_path(request.request.parent_sale_id)
    wrong_path = review_path(uuid4())
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        mismatch = await client.post(f"{wrong_path}/reviews", json=request.model_dump(mode="json"))
        assert mismatch.status_code == 422, mismatch.text
        saved = await client.post(f"{path}/reviews", json=request.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
        assert (await client.get(f"{wrong_path}/reviews/{request.operation_id}")).status_code == 404
        assert (await client.get(f"{path}/reviews/{uuid4()}")).status_code == 404
        assert (await client.get(f"{path}/reviews/not-a-uuid")).status_code == 422


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_review_http_rechecks_current_authority_on_all_routes(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    request = await save_review(db_session)
    path = review_path(request.request.parent_sale_id)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        saved = await client.post(f"{path}/reviews", json=request.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
        before = await facts(db_session)
        journals = await refund_workflow_counts(db_session)
        if denial == "permission":
            actor.permissions = {"pos.sell"}
            actor.permission_scopes = {"pos.sell": None}
        elif denial == "branch":
            actor.permission_scopes = {"pos.refund": frozenset({uuid4()})}
        elif denial == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        assert (
            await client.post(
                f"{path}/quote", json=request.request.selection.model_dump(mode="json")
            )
        ).status_code == 403
        assert (
            await client.post(f"{path}/reviews", json=request.model_dump(mode="json"))
        ).status_code == 403
        assert (await client.get(f"{path}/reviews/{request.operation_id}")).status_code == 403
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals


async def test_review_http_other_refund_author_cannot_recover_saved_review(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    request = await save_review(db_session)
    path = review_path(request.request.parent_sale_id)
    context = runtime(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        saved = await client.post(f"{path}/reviews", json=request.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    async with cart_actor(db_session) as actor:
        actor.user_id = other
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        assert (await client.get(f"{path}/reviews/{request.operation_id}")).status_code == 404
        quote = await client.post(
            f"{path}/quote", json=request.request.selection.model_dump(mode="json")
        )
        assert quote.status_code == 200, quote.text


async def test_readonly_tenant_can_quote_and_recover_review_but_cannot_save(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request = await save_review(db_session)
    path = review_path(request.request.parent_sale_id)
    context = runtime(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        saved = await client.post(f"{path}/reviews", json=request.model_dump(mode="json"))
        assert saved.status_code == 200, saved.text
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        quote = await client.post(
            f"{path}/quote", json=request.request.selection.model_dump(mode="json")
        )
        assert quote.status_code == 200, quote.text
        assert quote.json() == saved.json()["preview"]
        assert quote.headers["cache-control"] == "private, no-store"
        recovered = await client.get(f"{path}/reviews/{request.operation_id}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == saved.json()
        assert recovered.headers["cache-control"] == "private, no-store"
        assert (
            await client.post(f"{path}/reviews", json=request.model_dump(mode="json"))
        ).status_code == 422
        fresh = request.model_copy(update={"operation_id": uuid4()})
        assert (
            await client.post(f"{path}/reviews", json=fresh.model_dump(mode="json"))
        ).status_code == 422
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals
