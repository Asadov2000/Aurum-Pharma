"""Operator confirmations use real HTTP dependencies and RLS without money movement."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_electronic_refund_binding_api import binding_path
from tests.domains.pos.test_unit_electronic_refund_outcome import activated, confirmation, outcome
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_electronic_refund_revalidation import persist_review
from tests.domains.pos.test_unit_electronic_refund_review_api import refund_workflow_counts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def authorize_confirmation(actor: CurrentUser) -> None:
    actor.permissions = {"pos.refund", "pos.refund_external_confirm"}
    actor.permission_scopes = {permission: None for permission in actor.permissions}
    actor.password_verified_at = datetime.now(UTC)


async def test_confirmation_http_empty_partial_full_replay_and_read_have_no_financial_effects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, activation = await activated(db_session)
    payments = [p for p in activation.review.preview.payments if p.method in {"card", "qr"}]
    assert len(payments) == 2
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        empty = await client.get(path)
        assert empty.status_code == 200, empty.text
        assert empty.headers["cache-control"] == "private, no-store"
        assert empty.json()["state"] == "requires_reconciliation"
        assert empty.json()["confirmations"] == []
        assert set(empty.json()["missing_payment_ids"]) == {
            str(payment.original_payment_id) for payment in payments
        }
        for index, payment in enumerate(payments):
            evidence = confirmation(payment.original_payment_id).model_dump(mode="json")
            saved = await client.post(path, json=evidence)
            assert saved.status_code == 200, saved.text
            assert saved.headers["cache-control"] == "private, no-store"
            result = saved.json()
            assert len(result["confirmations"]) == index + 1
            assert result["state"] == (
                "requires_reconciliation" if index == 0 else "confirmed_evidence_only"
            )
            assert result["activation"] == activation.model_dump(mode="json")
            recorded = next(
                row
                for row in result["confirmations"]
                if row["original_payment_id"] == str(payment.original_payment_id)
            )
            assert recorded["amount"] == str(payment.amount)
            assert recorded["method"] == payment.method
            assert recorded["currency"] == "TJS"
            assert recorded["recorded_at"]
            for response in (await client.post(path, json=evidence), await client.get(path)):
                assert response.status_code == 200, response.text
                assert response.headers["cache-control"] == "private, no-store"
                assert response.json() == result
        assert result["missing_payment_ids"] == []
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        actor.password_verified_at = None
        recovered = await client.get(path)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == result
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals | {
        "pos_unit_electronic_refund_outcome": journals["pos_unit_electronic_refund_outcome"] + 2
    }


@pytest.mark.parametrize("proof", ["password", "mfa"])
async def test_confirmation_http_recent_proof_and_closed_shift_allow_evidence(
    client: AsyncClient, db_session: AsyncSession, proof: str
) -> None:
    request, activation = await activated(db_session)
    await close_shift(db_session, activation.shift_id)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        actor.password_verified_at = datetime.now(UTC) if proof == "password" else None
        actor.mfa_verified_at = datetime.now(UTC) if proof == "mfa" else None
        evidence = confirmation(payment.original_payment_id).model_dump(mode="json")
        response = await client.post(path, json=evidence)
        assert response.status_code == 200, response.text
        assert (await client.post(path, json=evidence)).json() == response.json()
    assert await facts(db_session) == before


@pytest.mark.parametrize("proof", ["missing", "stale", "future"])
async def test_confirmation_http_requires_recent_step_up_without_overriding_dependency(
    client: AsyncClient, db_session: AsyncSession, proof: str
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    evidence = confirmation(payment.original_payment_id).model_dump(mode="json")
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        value = (
            None
            if proof == "missing"
            else datetime.now(UTC) + timedelta(days=-2 if proof == "stale" else 2)
        )
        actor.password_verified_at = value
        actor.mfa_verified_at = value
        assert (await client.post(path, json=evidence)).status_code == 403
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert response.json()["confirmations"] == []


@pytest.mark.parametrize(
    "denial", ["refund", "external", "refund_branch", "external_branch", "disjoint"]
)
async def test_confirmation_http_intersects_both_permission_scopes(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    branch = runtime(db_session).stock.branch_id
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        if denial in {"refund", "external"}:
            actor.permissions.remove(
                "pos.refund" if denial == "refund" else "pos.refund_external_confirm"
            )
        elif denial == "disjoint":
            actor.permission_scopes = {
                "pos.refund": frozenset({branch}),
                "pos.refund_external_confirm": frozenset({uuid4()}),
            }
        else:
            permission = (
                "pos.refund" if denial == "refund_branch" else "pos.refund_external_confirm"
            )
            actor.permission_scopes[permission] = frozenset({uuid4()})
        response = await client.post(
            path, json=confirmation(payment.original_payment_id).model_dump(mode="json")
        )
        assert response.status_code == 403, response.text
        response = await client.get(path)
        assert response.status_code == (403 if denial in {"refund", "refund_branch"} else 200)
    assert (await outcome(db_session, request)).confirmations == ()


@pytest.mark.parametrize("tenant_wide", ["refund", "external", "neither"])
async def test_confirmation_http_accepts_common_branch_with_tenant_wide_or_narrow_scope(
    client: AsyncClient, db_session: AsyncSession, tenant_wide: str
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        branch = frozenset({runtime(db_session).stock.branch_id})
        actor.permission_scopes = {
            "pos.refund": None if tenant_wide == "refund" else branch,
            "pos.refund_external_confirm": None if tenant_wide == "external" else branch,
        }
        response = await client.post(
            path, json=confirmation(payment.original_payment_id).model_dump(mode="json")
        )
        assert response.status_code == 200, response.text


async def test_confirmation_http_changed_or_duplicate_evidence_conflicts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, activation = await activated(db_session)
    payments = [p for p in activation.review.preview.payments if p.method in {"card", "qr"}]
    first = confirmation(payments[0].original_payment_id)
    saved = await outcome(db_session, request, first)
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        for evidence in (
            confirmation(payments[0].original_payment_id),
            first.model_copy(update={"original_payment_id": payments[1].original_payment_id}),
        ):
            response = await client.post(path, json=evidence.model_dump(mode="json"))
            assert response.status_code == 409, response.text
        assert (await client.get(path)).json() == saved.model_dump(mode="json")


@pytest.mark.parametrize("kind", ["cash", "unknown"])
async def test_confirmation_http_rejects_non_electronic_allocations(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    request, activation = await activated(db_session)
    payment = next(
        p.original_payment_id for p in activation.review.preview.payments if p.method == "cash"
    )
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        response = await client.post(
            path, json=confirmation(payment if kind == "cash" else uuid4()).model_dump(mode="json")
        )
        assert response.status_code == 422, response.text
        assert (await client.get(path)).json()["confirmations"] == []


async def test_confirmation_http_requires_activation_and_validates_paths_and_body(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, review = await persist_review(db_session)
    payment = next(p.original_payment_id for p in review.preview.payments if p.method == "card")
    body = confirmation(payment).model_dump(mode="json")
    parent = request.request.parent_sale_id
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        for target, status in (
            (binding_path(parent, request.operation_id), 404),
            (binding_path(parent, uuid4()), 404),
            (binding_path(uuid4(), request.operation_id), 404),
            (binding_path(parent, "bad"), 422),
            (binding_path(parent, "00000000-0000-1000-8000-000000000001"), 422),
            (binding_path("bad", request.operation_id), 422),
        ):
            path = f"{target}/confirmations"
            assert (await client.post(path, json=body)).status_code == status
            assert (await client.get(path)).status_code == status
        path = f"{binding_path(parent, request.operation_id)}/confirmations"
        for invalid in (
            {},
            body | {"amount": "1.00"},
            body | {"method": "card"},
            body | {"currency": "TJS"},
            body | {"original_payment_id": "bad"},
            body | {"terminal_id": " "},
            body | {"document_number": "a\nb"},
            body | {"terminal_id": 12},
            body | {"document_number": "a" * 129},
        ):
            assert (await client.post(path, json=invalid)).status_code == 422
        assert (await client.post(path)).status_code == 422


async def test_confirmation_http_readonly_recovers_but_rejects_replay_and_new_evidence(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, activation = await activated(db_session)
    payments = [p for p in activation.review.preview.payments if p.method in {"card", "qr"}]
    evidence = confirmation(payments[0].original_payment_id)
    saved = await outcome(db_session, request, evidence)
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
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        for item in (evidence, confirmation(payments[1].original_payment_id)):
            response = await client.post(path, json=item.model_dump(mode="json"))
            assert response.status_code == 422, response.text
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        actor.password_verified_at = None
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert response.json() == saved.model_dump(mode="json")
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals


async def test_confirmation_http_other_author_cannot_discover_evidence(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    evidence = confirmation(payment.original_payment_id)
    await outcome(db_session, request, evidence)
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        actor.user_id = other
        assert (await client.post(path, json=evidence.model_dump(mode="json"))).status_code == 404
        assert (await client.get(path)).status_code == 404


@pytest.mark.parametrize("context", ["actor", "tenant"])
async def test_confirmation_http_rejects_request_and_database_context_mismatch(
    client: AsyncClient, db_session: AsyncSession, context: str
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    path = f"{binding_path(request.request.parent_sale_id, request.operation_id)}/confirmations"
    async with cart_actor(db_session) as actor:
        authorize_confirmation(actor)
        if context == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        assert (
            await client.post(
                path, json=confirmation(payment.original_payment_id).model_dump(mode="json")
            )
        ).status_code == 403
        assert (await client.get(path)).status_code == 403
