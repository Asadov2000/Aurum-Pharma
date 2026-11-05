"""HTTP activation saves one marker and recovers it without financial effects."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_electronic_refund_activation import activate
from tests.domains.pos.test_unit_electronic_refund_binding import binding
from tests.domains.pos.test_unit_electronic_refund_binding_api import binding_path
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_electronic_refund_revalidation import persist_review
from tests.domains.pos.test_unit_electronic_refund_review_api import refund_workflow_counts
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def original_shift(session: AsyncSession, parent_sale_id: UUID) -> UUID:
    shift_id = await session.scalar(
        text("SELECT shift_id FROM sale WHERE id=:id"), {"id": parent_sale_id}
    )
    assert isinstance(shift_id, UUID)
    return shift_id


async def test_activation_http_replay_and_closed_shift_recovery_have_no_financial_effects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, review = await persist_review(db_session)
    await binding(db_session, request, "bind")
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    shift_id = await original_shift(db_session, request.request.parent_sale_id)
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": frozenset({runtime(db_session).stock.branch_id})}
        assert (await client.get(f"{path}/activation")).status_code == 404
        saved = await client.post(f"{path}/activate", json={"shift_id": str(shift_id)})
        assert saved.status_code == 200, saved.text
        assert saved.headers["cache-control"] == "private, no-store"
        assert saved.json()["state"] == "activation_saved"
        assert saved.json()["operation_id"] == str(request.operation_id)
        assert saved.json()["review"] == review.model_dump(mode="json")
        assert saved.json()["shift_id"] == str(shift_id)
        assert saved.json()["activated_at"]
        for response in (
            await client.post(f"{path}/activate", json={"shift_id": str(shift_id)}),
            await client.get(f"{path}/activation"),
        ):
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            assert response.json() == saved.json()
        assert (
            await client.post(f"{path}/activate", json={"shift_id": str(uuid4())})
        ).status_code == 409
        assert await facts(db_session) == before
        await close_shift(db_session, shift_id)
        after_closure = await facts(db_session)
        for response in (
            await client.post(f"{path}/activate", json={"shift_id": str(shift_id)}),
            await client.get(f"{path}/activation"),
        ):
            assert response.status_code == 200, response.text
            assert response.json() == saved.json()
        assert await facts(db_session) == after_closure
    assert await refund_workflow_counts(db_session) == journals | {
        "pos_unit_electronic_refund_activation": journals["pos_unit_electronic_refund_activation"]
        + 1
    }


@pytest.mark.parametrize("preparation", ["missing", "cancelled"])
async def test_activation_http_requires_active_preparation(
    client: AsyncClient, db_session: AsyncSession, preparation: str
) -> None:
    request, _ = await persist_review(db_session)
    if preparation == "cancelled":
        await binding(db_session, request, "bind")
        await binding(db_session, request, "cancel")
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    shift_id = await original_shift(db_session, request.request.parent_sale_id)
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        response = await client.post(f"{path}/activate", json={"shift_id": str(shift_id)})
        assert response.status_code == 409, response.text
        assert (await client.get(f"{path}/activation")).status_code == 404
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals


@pytest.mark.parametrize("invalid", ["missing", "closed", "inactive_register", "other_register"])
async def test_activation_http_requires_valid_own_original_register_shift(
    client: AsyncClient, db_session: AsyncSession, invalid: str
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    parent = request.request.parent_sale_id
    shift_id = await original_shift(db_session, parent)
    if invalid == "closed":
        await close_shift(db_session, shift_id)
    elif invalid == "inactive_register":
        await db_session.execute(
            text(
                "UPDATE register SET is_active=false WHERE id=(SELECT register_id FROM sale "
                "WHERE id=:id)"
            ),
            {"id": parent},
        )
    elif invalid == "other_register":
        other = await checkout_source(db_session)
        shift_id = other.sale.shift_id
    else:
        shift_id = uuid4()
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    path = binding_path(parent, request.operation_id)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        response = await client.post(f"{path}/activate", json={"shift_id": str(shift_id)})
        assert response.status_code == 422, response.text
        assert (await client.get(f"{path}/activation")).status_code == 404
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_activation_http_rechecks_authority_on_write_and_recovery(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    saved = await activate(db_session, request)
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
        assert (
            await client.post(f"{path}/activate", json={"shift_id": str(saved.shift_id)})
        ).status_code == 403
        assert (await client.get(f"{path}/activation")).status_code == 403
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals


async def test_activation_http_other_author_cannot_discover_saved_marker(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    saved = await activate(db_session, request)
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
        assert (
            await client.post(f"{path}/activate", json={"shift_id": str(saved.shift_id)})
        ).status_code == 404
        assert (await client.get(f"{path}/activation")).status_code == 404


async def test_activation_http_validates_identifiers_and_strict_request_body(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    parent = request.request.parent_sale_id
    shift_id = await original_shift(db_session, parent)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        for path, status in (
            (binding_path(uuid4(), request.operation_id), 404),
            (binding_path(parent, uuid4()), 404),
            (binding_path(parent, "not-a-uuid"), 422),
            (binding_path(parent, "00000000-0000-1000-8000-000000000001"), 422),
            (binding_path("not-a-uuid", request.operation_id), 422),
        ):
            assert (
                await client.post(f"{path}/activate", json={"shift_id": str(shift_id)})
            ).status_code == status
            assert (await client.get(f"{path}/activation")).status_code == status
        path = binding_path(parent, request.operation_id)
        for body in (
            {},
            {"shift_id": None},
            {"shift_id": "bad"},
            {"shift_id": str(shift_id), "amount": "10.00"},
        ):
            response = await client.post(f"{path}/activate", json=body)
            assert response.status_code == 422, response.text
        assert (await client.post(f"{path}/activate")).status_code == 422
    assert await refund_workflow_counts(db_session) == journals


async def test_activation_http_readonly_recovers_but_denies_replay_and_new_marker(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    saved = await activate(db_session, request)
    second, _ = await persist_review(db_session)
    await binding(db_session, second, "bind")
    second_shift = await original_shift(db_session, second.request.parent_sale_id)
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
    journals = await refund_workflow_counts(db_session)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        response = await client.get(f"{path}/activation")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json() == saved.model_dump(mode="json")
        for target, shift_id in (
            (path, saved.shift_id),
            (binding_path(second.request.parent_sale_id, second.operation_id), second_shift),
        ):
            assert (
                await client.post(f"{target}/activate", json={"shift_id": str(shift_id)})
            ).status_code == 422
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals


async def test_activation_http_rejects_another_cashiers_original_register_shift(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    parent = request.request.parent_sale_id
    shift_id = await original_shift(db_session, parent)
    register_id = await db_session.scalar(
        text("SELECT register_id FROM sale WHERE id=:id"), {"id": parent}
    )
    assert isinstance(register_id, UUID)
    await close_shift(db_session, shift_id)
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.sell", "pos.refund")
    )
    await set_receiving_context(db_session, context.stock, other)
    shift = await POSService(POSRepository(db_session)).open_shift(
        tenant_id=context.stock.tenant_id,
        register_id=register_id,
        opened_by_user_id=other,
        opening_cash=Decimal("0.00"),
        allowed_branch_ids={context.stock.branch_id},
    )
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    path = binding_path(parent, request.operation_id)
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        response = await client.post(f"{path}/activate", json={"shift_id": str(shift.id)})
        assert response.status_code == 422, response.text
        assert (await client.get(f"{path}/activation")).status_code == 404
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals
