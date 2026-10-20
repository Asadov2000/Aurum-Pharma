"""HTTP posting uses real financial rows and recovers one saved electronic return."""

from decimal import Decimal
from uuid import UUID, uuid4

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
from tests.domains.pos.test_unit_electronic_refund_posting import post, reconciled
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_electronic_refund_review_api import refund_workflow_counts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def authorize_posting(actor: CurrentUser) -> None:
    actor.permissions = {"pos.refund"}
    actor.permission_scopes = {"pos.refund": None}


async def test_posting_http_exact_allocations_receipt_replay_and_recovery(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, shift_id = await reconciled(db_session)
    evidence = await outcome(db_session, request)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        assert actor.password_verified_at is None and actor.mfa_verified_at is None
        assert (await client.get(f"{path}/posting")).status_code == 404
        response = await client.post(f"{path}/post", json={"shift_id": str(shift_id)})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        saved = response.json()
        assert saved["operation_id"] == str(request.operation_id)
        assert saved["parent_sale_id"] == str(request.request.parent_sale_id)
        assert saved["sale_id"] != saved["parent_sale_id"]
        receipt = saved["receipt"]
        assert receipt["document_kind"] == "physical_electronic_refund_receipt"
        expected = {
            str(part.original_payment_id): part
            for part in evidence.activation.review.preview.payments
            if part.amount > 0
        }
        confirmations = {str(part.original_payment_id): part for part in evidence.confirmations}
        assert {part["original_payment_id"] for part in receipt["payments"]} == set(expected)
        assert len(receipt["payments"]) == len(expected)
        assert {part["method"] for part in receipt["payments"]} == {"cash", "card", "qr"}
        assert sum(Decimal(part["amount"]) for part in receipt["payments"]) == Decimal(
            saved["total_amount"]
        )
        for part in receipt["payments"]:
            frozen = expected[part["original_payment_id"]]
            assert (part["method"], part["amount"], part["currency"]) == (
                frozen.method,
                str(frozen.amount),
                frozen.currency,
            )
            if frozen.method == "cash":
                assert part["terminal_id"] is None and part["document_number"] is None
            else:
                recorded = confirmations[part["original_payment_id"]]
                assert (part["terminal_id"], part["document_number"]) == (
                    recorded.terminal_id,
                    recorded.document_number,
                )
        assert receipt["lines"][0]["base_units"] == 1
        assert receipt["lines"][0]["offset"] == 0
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        snapshot = await db_session.scalar(
            text("SELECT receipt_snapshot FROM sale WHERE id=:id"), {"id": UUID(saved["sale_id"])}
        )
        assert snapshot == receipt
        after = await facts(db_session)
        journals = await refund_workflow_counts(db_session)
        assert len(after["sale"]) == len(before["sale"]) + 1
        assert len(after["sale_payment"]) == len(before["sale_payment"]) + len(expected)
        for table in before:
            if table.startswith("unit_stock_") or table in {
                "pos_payment_attempt",
                "pos_refund_attempt",
            }:
                assert after[table] == before[table]
        for recovered in (
            await client.post(f"{path}/post", json={"shift_id": str(shift_id)}),
            await client.get(f"{path}/posting"),
        ):
            assert recovered.status_code == 200, recovered.text
            assert recovered.headers["cache-control"] == "private, no-store"
            assert recovered.json() == saved
    assert await facts(db_session) == after
    assert await refund_workflow_counts(db_session) == journals


@pytest.mark.parametrize("confirmation_count", [0, 1])
async def test_posting_http_partial_evidence_cannot_move_money(
    client: AsyncClient, db_session: AsyncSession, confirmation_count: int
) -> None:
    request, activation = await activated(db_session)
    payments = [p for p in activation.review.preview.payments if p.method in {"card", "qr"}]
    for payment in payments[:confirmation_count]:
        await outcome(db_session, request, confirmation(payment.original_payment_id))
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        response = await client.post(f"{path}/post", json={"shift_id": str(activation.shift_id)})
        assert response.status_code == 409, response.text
        assert (await client.get(f"{path}/posting")).status_code == 404
    assert await facts(db_session) == before


async def test_posting_http_changed_replay_conflicts_without_duplicate_rows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, shift_id = await reconciled(db_session)
    saved = await post(db_session, request, shift_id)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        for changed in (
            {"shift_id": str(uuid4())},
            {"shift_id": str(shift_id), "comment": "Changed reason"},
            {"shift_id": str(shift_id), "reason_code": "other"},
        ):
            response = await client.post(f"{path}/post", json=changed)
            assert response.status_code == 409, response.text
        assert (await client.get(f"{path}/posting")).json() == saved.model_dump(mode="json")
    assert await facts(db_session) == before


async def test_posting_http_strict_body_and_resource_paths(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, shift_id = await reconciled(db_session)
    parent = request.request.parent_sale_id
    path = binding_path(parent, request.operation_id)
    body = {"shift_id": str(shift_id)}
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        for invalid in (
            {},
            {"shift_id": "bad"},
            body | {"amount": "1.00"},
            body | {"reason_code": "unknown"},
            body | {"comment": 12},
            body | {"comment": "a" * 501},
        ):
            assert (await client.post(f"{path}/post", json=invalid)).status_code == 422
        assert (await client.post(f"{path}/post")).status_code == 422
        for target, status in (
            (binding_path(parent, uuid4()), 404),
            (binding_path(uuid4(), request.operation_id), 404),
            (binding_path(parent, "bad"), 422),
            (binding_path(parent, "00000000-0000-1000-8000-000000000001"), 422),
            (binding_path("bad", request.operation_id), 422),
        ):
            assert (await client.post(f"{target}/post", json=body)).status_code == status
            assert (await client.get(f"{target}/posting")).status_code == status
    assert await facts(db_session) == before


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_posting_http_rechecks_authority_for_write_and_recovery(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    request, shift_id = await reconciled(db_session)
    await post(db_session, request, shift_id)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        if denial == "permission":
            actor.permissions = set()
        elif denial == "branch":
            actor.permission_scopes = {"pos.refund": frozenset({uuid4()})}
        elif denial == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        assert (
            await client.post(f"{path}/post", json={"shift_id": str(shift_id)})
        ).status_code == 403
        assert (await client.get(f"{path}/posting")).status_code == 403
    assert await facts(db_session) == before


async def test_posting_http_closed_shift_blocks_new_post(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, shift_id = await reconciled(db_session)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    await close_shift(db_session, shift_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        response = await client.post(f"{path}/post", json={"shift_id": str(shift_id)})
        assert response.status_code == 422, response.text
        assert (await client.get(f"{path}/posting")).status_code == 404
    assert await facts(db_session) == before


async def test_posting_http_closed_shift_saved_replay_and_read(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, shift_id = await reconciled(db_session)
    saved = await post(db_session, request, shift_id)
    await close_shift(db_session, shift_id)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        for response in (
            await client.post(f"{path}/post", json={"shift_id": str(shift_id)}),
            await client.get(f"{path}/posting"),
        ):
            assert response.status_code == 200, response.text
            assert response.json() == saved.model_dump(mode="json")
    assert await facts(db_session) == before


@pytest.mark.parametrize("already_posted", [False, True])
async def test_posting_http_readonly_allows_recovery_but_rejects_every_write(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    already_posted: bool,
) -> None:
    request, shift_id = await reconciled(db_session)
    saved = await post(db_session, request, shift_id) if already_posted else None
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
        authorize_posting(actor)
        response = await client.post(f"{path}/post", json={"shift_id": str(shift_id)})
        assert response.status_code == 422, response.text
        response = await client.get(f"{path}/posting")
        assert response.status_code == (200 if already_posted else 404), response.text
        if saved is not None:
            assert response.json() == saved.model_dump(mode="json")
    assert await facts(db_session) == before


async def test_posting_http_other_author_cannot_discover_saved_return(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    request, shift_id = await reconciled(db_session)
    await post(db_session, request, shift_id)
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        actor.user_id = other
        assert (
            await client.post(f"{path}/post", json={"shift_id": str(shift_id)})
        ).status_code == 404
        assert (await client.get(f"{path}/posting")).status_code == 404


async def test_posting_http_caller_rollback_removes_all_new_financial_facts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, shift_id = await reconciled(db_session)
    path = binding_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    journals = await refund_workflow_counts(db_session)
    async with cart_actor(db_session) as actor:
        authorize_posting(actor)
        async with db_session.begin_nested() as caller:
            response = await client.post(f"{path}/post", json={"shift_id": str(shift_id)})
            assert response.status_code == 200, response.text
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await caller.rollback()
        assert (await client.get(f"{path}/posting")).status_code == 404
    assert await facts(db_session) == before
    assert await refund_workflow_counts(db_session) == journals
