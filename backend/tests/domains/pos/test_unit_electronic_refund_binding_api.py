"""HTTP preparation and cancellation use real RLS journals without paying a refund."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_intent_service import UnitElectronicRefundIntentService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_unit_electronic_refund_activation import activate
from tests.domains.pos.test_unit_electronic_refund_binding import binding
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_electronic_refund_revalidation import persist_review
from tests.domains.pos.test_unit_electronic_refund_review_api import refund_workflow_counts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def binding_path(parent_sale_id: object, operation_id: object) -> str:
    return f"/api/v1/sales/{parent_sale_id}/unit-electronic-refunds/reviews/{operation_id}"


async def test_binding_http_replay_recovery_and_cancel_preserve_financial_facts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, saved = await persist_review(db_session)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": frozenset({runtime(db_session).stock.branch_id})}
        bound = await client.post(f"{path}/bind")
        assert bound.status_code == 200, bound.text
        assert bound.headers["cache-control"] == "private, no-store"
        assert bound.json()["state"] == "bound"
        assert bound.json()["operation_id"] == str(request.operation_id)
        assert bound.json()["review_revision_hash"] == saved.preview.revision_hash
        assert bound.json()["bound_at"] and bound.json()["cancelled_at"] is None
        for response in (
            await client.post(f"{path}/bind"),
            await client.get(f"{path}/binding"),
        ):
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            assert response.json() == bound.json()
        cancelled = await client.post(f"{path}/cancel-binding")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.headers["cache-control"] == "private, no-store"
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["bound_at"] == bound.json()["bound_at"]
        assert cancelled.json()["cancelled_at"]
        for response in (
            await client.post(f"{path}/cancel-binding"),
            await client.get(f"{path}/binding"),
        ):
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            assert response.json() == cancelled.json()
        assert (await client.post(f"{path}/bind")).status_code == 409
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals | {
        "pos_unit_electronic_refund_binding": journals["pos_unit_electronic_refund_binding"] + 1
    }
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_electronic_refund_binding_cancel WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 1
    )


async def test_binding_http_early_cancellation_blocks_delayed_binding(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, _ = await persist_review(db_session)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        cancelled = await client.post(f"{path}/cancel-binding")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["bound_at"] is None
        assert (await client.post(f"{path}/bind")).status_code == 409
        assert (await client.get(f"{path}/binding")).json() == cancelled.json()
    assert await facts(db_session) == before
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_electronic_refund_binding WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )


async def test_binding_http_second_review_cannot_claim_bound_source(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, _ = await persist_review(db_session)
    second = request.model_copy(update={"operation_id": uuid4()})
    context = runtime(db_session)
    await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(db_session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=second,
    )
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    other_path = binding_path(second.request.parent_sale_id, second.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        bound = await client.post(f"{path}/bind")
        assert bound.status_code == 200, bound.text
        assert (await client.post(f"{other_path}/bind")).status_code == 409
        assert (await client.get(f"{other_path}/binding")).status_code == 404
        assert (await client.get(f"{path}/binding")).json() == bound.json()
    assert await facts(db_session) == before


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_binding_http_rechecks_authority_for_all_routes(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        if denial == "permission":
            actor.permissions = {"pos.sell"}
            actor.permission_scopes = {"pos.sell": None}
        elif denial == "branch":
            actor.permission_scopes = {"pos.refund": frozenset({uuid4()})}
        elif denial == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        assert (await client.post(f"{path}/bind")).status_code == 403
        assert (await client.post(f"{path}/cancel-binding")).status_code == 403
        assert (await client.get(f"{path}/binding")).status_code == 403
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals
    assert (await binding(db_session, request, "read")).state == "bound"


async def test_binding_http_scopes_identifiers_and_rejects_malformed_uuid(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, _ = await persist_review(db_session)
    parent = request.request.parent_sale_id
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        for path, status in (
            (binding_path(uuid4(), request.operation_id), 404),
            (binding_path(parent, uuid4()), 404),
            (binding_path(parent, "not-a-uuid"), 422),
            (binding_path(parent, "00000000-0000-1000-8000-000000000001"), 422),
        ):
            assert (await client.post(f"{path}/bind")).status_code == status
            assert (await client.post(f"{path}/cancel-binding")).status_code == status
            assert (await client.get(f"{path}/binding")).status_code == status


async def test_binding_http_other_refund_author_cannot_access_preparation(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    async with cart_actor(db_session) as actor:
        actor.user_id = other
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        assert (await client.post(f"{path}/bind")).status_code == 404
        assert (await client.post(f"{path}/cancel-binding")).status_code == 404
        assert (await client.get(f"{path}/binding")).status_code == 404


async def test_binding_http_readonly_recovery_allows_no_mutation(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, _ = await persist_review(db_session)
    saved = await binding(db_session, request, "bind")
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        recovered = await client.get(f"{path}/binding")
        assert recovered.status_code == 200, recovered.text
        assert recovered.headers["cache-control"] == "private, no-store"
        assert recovered.json() == saved.model_dump(mode="json")
        assert (await client.post(f"{path}/bind")).status_code == 422
        assert (await client.post(f"{path}/cancel-binding")).status_code == 422
    assert await facts(db_session) == before


async def test_binding_http_cancellation_after_internal_activation_hits_database_guard(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    await activate(db_session, request)
    before = await facts(db_session)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        cancelled = await client.post(f"{path}/cancel-binding")
        assert cancelled.status_code == 409, cancelled.text
        recovered = await client.get(f"{path}/binding")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["state"] == "bound"
    assert await facts(db_session) == before
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_electronic_refund_binding_cancel WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )
