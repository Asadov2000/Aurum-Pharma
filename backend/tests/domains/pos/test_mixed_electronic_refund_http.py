"""Real mixed HTTP lifecycle posts once and recovers under current tenant authority."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SaveMixedElectronicRefundIntentRequest,
)
from app.domains.pos.mixed_electronic_refund_posting_schemas import PostedMixedElectronicRefund
from app.domains.pos.mixed_electronic_refund_preview import MixedElectronicRefundPreview
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_mixed_electronic_refund_intent import save_review
from tests.domains.pos.test_mixed_electronic_refund_outcome import activated, confirmation
from tests.domains.pos.test_mixed_electronic_refund_posting import facts, post, reconciled
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def base_path(parent: object) -> str:
    return f"/api/v1/sales/{parent}/mixed-electronic-refunds"


def review_path(parent: object, operation: object) -> str:
    return f"{base_path(parent)}/reviews/{operation}"


def authorize(actor: CurrentUser, *, evidence: bool = False) -> None:
    actor.permissions = {"pos.refund"}
    if evidence:
        actor.permissions.add("pos.refund_external_confirm")
    actor.permission_scopes = {permission: None for permission in actor.permissions}
    actor.password_verified_at = datetime.now(UTC) if evidence else None


def assert_posted_result(
    result: PostedMixedElectronicRefund,
    quote: MixedElectronicRefundPreview,
    request: SaveMixedElectronicRefundIntentRequest,
    before: dict[str, list[object]],
    after: dict[str, list[object]],
) -> None:
    """Validate the immutable receipt against its quote and the actual financial rows."""
    assert result.sale_id != request.request.parent_sale_id
    assert result.parent_sale_id == request.request.parent_sale_id
    assert result.operation_id == request.operation_id
    receipt = result.receipt
    assert receipt.document_kind == "mixed_electronic_refund_receipt"
    assert receipt.schema_version == 4
    assert {line.inventory_kind for line in receipt.lines} == {"legacy_pack", "physical_unit"}
    expected = {part.original_payment_id: part for part in quote.payments if part.amount > 0}
    assert {part.original_payment_id for part in receipt.payments} == set(expected)
    assert sum((part.amount for part in receipt.payments), Decimal()) == result.total_amount
    for part in receipt.payments:
        original = expected[part.original_payment_id]
        assert (part.method, part.amount, part.currency) == (
            original.method,
            original.amount,
            "TJS",
        )
    assert len(after["sale"]) == len(before["sale"]) + 1
    assert len(after["sale_payment"]) == len(before["sale_payment"]) + len(expected)
    for table in before:
        if table.startswith("unit_stock_") or table in {
            "pos_payment_attempt",
            "pos_refund_attempt",
        }:
            assert after[table] == before[table]


async def save_and_bind_review(
    client: AsyncClient, request: SaveMixedElectronicRefundIntentRequest
) -> tuple[Response, Response]:
    """Check the persisted preparation before any external confirmation or posting."""
    base = base_path(request.request.parent_sale_id)
    path = review_path(request.request.parent_sale_id, request.operation_id)
    quote = await client.post(
        f"{base}/quote", json=request.request.selection.model_dump(mode="json")
    )
    assert quote.status_code == 200, quote.text
    assert quote.headers["cache-control"] == "private, no-store"
    assert quote.json()["revision_hash"] == request.request.expected_revision_hash
    saved = await client.post(f"{base}/reviews", json=request.model_dump(mode="json"))
    assert saved.status_code == 200, saved.text
    assert saved.json()["preview"] == quote.json()
    assert (await client.get(path)).json() == saved.json()
    bound = await client.post(f"{path}/bind")
    assert bound.status_code == 200, bound.text
    assert bound.json()["state"] == "bound"
    assert (await client.get(f"{path}/binding")).json() == bound.json()
    return quote, saved


async def test_mixed_http_full_lifecycle_exact_money_replay_and_saved_recovery(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request = await save_review(db_session)
    parent, operation = request.request.parent_sale_id, request.operation_id
    base, path = base_path(parent), review_path(parent, operation)
    shift = await db_session.scalar(text("SELECT shift_id FROM sale WHERE id=:id"), {"id": parent})
    assert isinstance(shift, UUID)
    async with cart_actor(db_session) as actor:
        authorize(actor, evidence=True)
        quote, saved = await save_and_bind_review(client, request)
        active = await client.post(f"{path}/activate", json={"shift_id": str(shift)})
        assert active.status_code == 200, active.text
        assert active.json()["review"] == saved.json()
        assert (await client.get(f"{path}/activation")).json() == active.json()
        assert (await client.post(f"{path}/cancel-binding")).status_code == 409
        evidence = await client.get(f"{path}/confirmations")
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["state"] == "requires_reconciliation"
        payments = [part for part in quote.json()["payments"] if part["method"] != "cash"]
        assert len(payments) == 2
        for index, payment in enumerate(payments):
            premature = await client.post(f"{path}/post", json={"shift_id": str(shift)})
            assert premature.status_code == 409, premature.text
            assert (await client.get(f"{path}/posting")).status_code == 404
            body = confirmation(UUID(payment["original_payment_id"])).model_dump(mode="json")
            evidence = await client.post(f"{path}/confirmations", json=body)
            assert evidence.status_code == 200, evidence.text
            assert evidence.json()["state"] == (
                "requires_reconciliation" if index == 0 else "confirmed_evidence_only"
            )
            assert (await client.post(f"{path}/confirmations", json=body)).json() == evidence.json()
            assert (await client.get(f"{path}/confirmations")).json() == evidence.json()
        before = await facts(db_session)
        actor.password_verified_at = None
        actor.permissions.remove("pos.refund_external_confirm")
        response = await client.post(f"{path}/post", json={"shift_id": str(shift)})
        assert response.status_code == 200, response.text
        result = response.json()
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        after = await facts(db_session)
        assert_posted_result(
            PostedMixedElectronicRefund.model_validate(result),
            MixedElectronicRefundPreview.model_validate(quote.json()),
            request,
            before,
            after,
        )
        for replay in (
            await client.post(f"{base}/reviews", json=request.model_dump(mode="json")),
            await client.post(f"{path}/bind"),
            await client.post(f"{path}/activate", json={"shift_id": str(shift)}),
            await client.post(f"{path}/post", json={"shift_id": str(shift)}),
            await client.get(f"{path}/posting"),
        ):
            assert replay.status_code == 200, replay.text
            assert replay.headers["cache-control"] == "private, no-store"
        assert (await client.get(f"{path}/posting")).json() == result
        assert (
            await client.post(f"{path}/post", json={"shift_id": str(shift), "comment": "Changed"})
        ).status_code == 409
        assert (
            await client.post(f"{path}/post", json={"shift_id": str(uuid4())})
        ).status_code == 409
    assert await facts(db_session) == after


async def test_mixed_http_cancellation_is_final_before_activation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request = await save_review(db_session)
    base = base_path(request.request.parent_sale_id)
    path = review_path(request.request.parent_sale_id, request.operation_id)
    async with cart_actor(db_session) as actor:
        authorize(actor)
        assert (
            await client.post(f"{base}/reviews", json=request.model_dump(mode="json"))
        ).status_code == 200
        assert (await client.post(f"{path}/bind")).status_code == 200
        cancelled = await client.post(f"{path}/cancel-binding")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["state"] == "cancelled"
        assert (await client.post(f"{path}/cancel-binding")).json() == cancelled.json()
        assert (await client.get(f"{path}/binding")).json() == cancelled.json()
        assert (await client.post(f"{path}/bind")).status_code == 409


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_mixed_http_current_authority_protects_commands_and_all_recovery(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    request, shift = await reconciled(db_session)
    await post(db_session, request, shift)
    base = base_path(request.request.parent_sale_id)
    path = review_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize(actor, evidence=True)
        if denial == "permission":
            actor.permissions.remove("pos.refund")
        elif denial == "branch":
            actor.permission_scopes["pos.refund"] = frozenset({uuid4()})
        elif denial == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        for target in (
            path,
            f"{path}/binding",
            f"{path}/activation",
            f"{path}/confirmations",
            f"{path}/posting",
        ):
            response = await client.get(target)
            assert response.status_code == 403, response.text
        for target, body in (
            (f"{base}/quote", request.request.selection.model_dump(mode="json")),
            (f"{base}/reviews", request.model_dump(mode="json")),
            (f"{path}/bind", None),
            (f"{path}/cancel-binding", None),
            (f"{path}/activate", {"shift_id": str(shift)}),
            (f"{path}/confirmations", confirmation(uuid4()).model_dump(mode="json")),
            (f"{path}/post", {"shift_id": str(shift)}),
        ):
            response = await client.post(target, json=body)
            assert response.status_code == 403, response.text
    assert await facts(db_session) == before


async def test_mixed_http_foreign_valid_refund_author_cannot_recover_saved_records(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    request, shift = await reconciled(db_session)
    await post(db_session, request, shift)
    await db_session.commit()
    context = runtime(db_session)
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    path = review_path(request.request.parent_sale_id, request.operation_id)
    async with cart_actor(db_session) as actor:
        authorize(actor)
        actor.user_id = other
        for suffix in ("", "/binding", "/activation", "/confirmations", "/posting"):
            response = await client.get(f"{path}{suffix}")
            assert response.status_code == 404, response.text


async def test_mixed_http_readonly_recovers_preparation_and_quote_without_commands(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, activation = await activated(db_session)
    await db_session.commit()
    context = runtime(db_session)
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    base = base_path(request.request.parent_sale_id)
    path = review_path(request.request.parent_sale_id, request.operation_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize(actor, evidence=True)
        for suffix in ("", "/binding", "/activation", "/confirmations"):
            recovered = await client.get(f"{path}{suffix}")
            assert recovered.status_code == 200, recovered.text
            assert recovered.headers["cache-control"] == "private, no-store"
        quote = await client.post(
            f"{base}/quote", json=request.request.selection.model_dump(mode="json")
        )
        assert quote.status_code == 200, quote.text
        assert (await client.get(f"{path}/posting")).status_code == 404
        for target, body in (
            (f"{base}/reviews", request.model_dump(mode="json")),
            (f"{path}/bind", None),
            (f"{path}/cancel-binding", None),
            (f"{path}/activate", {"shift_id": str(activation.shift_id)}),
            (f"{path}/confirmations", confirmation(uuid4()).model_dump(mode="json")),
            (f"{path}/post", {"shift_id": str(activation.shift_id)}),
        ):
            assert (await client.post(target, json=body)).status_code == 422
    assert await facts(db_session) == before


async def test_mixed_http_closed_shift_and_readonly_preserve_posted_result(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, shift = await reconciled(db_session)
    result = await post(db_session, request, shift)
    await close_shift(db_session, shift)
    path = review_path(request.request.parent_sale_id, request.operation_id)
    async with cart_actor(db_session) as actor:
        authorize(actor)
        replay = await client.post(f"{path}/post", json={"shift_id": str(shift)})
        assert replay.status_code == 200, replay.text
        assert replay.json() == result.model_dump(mode="json")
    await db_session.commit()
    context = runtime(db_session)
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    async with cart_actor(db_session) as actor:
        authorize(actor)
        response = await client.get(f"{path}/posting")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json() == result.model_dump(mode="json")
        assert (await client.post(f"{path}/post", json={"shift_id": str(shift)})).status_code == 422
    assert await facts(db_session) == before
