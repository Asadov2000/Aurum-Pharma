"""Public physical refund flow uses real app RLS and immutable monetary history."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_refund import close_shift, facts, refund
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields, refund_actor
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def path(parent: UUID) -> str:
    return f"/api/v1/sales/{parent}/physical-cash-refunds"


async def source(session: AsyncSession) -> PhysicalCheckoutResultV3:
    prepared = await checkout_source(session)
    await put_physical(session, prepared.sale.id, sale_format="pack", quantity=2)
    original = await complete(session, await saved_request(session, prepared.sale))
    repository = FoundationRepository(session)
    settings = await repository.get_settings(original.tenant_id)
    assert settings is not None
    await repository.update_settings(settings, refund_reason_mode="off")
    return original


def items(original: PhysicalCheckoutResultV3, sale_format: str = "unit") -> list[dict[str, object]]:
    return [{"line_id": str(original.lines[0].id), "sale_format": sale_format, "quantity": 1}]


async def request(
    client: AsyncClient, original: PhysicalCheckoutResultV3, sale_format: str = "unit"
) -> dict[str, object]:
    selection = items(original, sale_format)
    response = await client.post(f"{path(original.sale_id)}/quote", json={"items": selection})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert_no_cost_fields(response.json())
    return {
        "operation_id": str(uuid4()),
        "shift_id": str(original.shift_id),
        "items": selection,
        "expected_total_amount": response.json()["total_amount"],
        "expected_revision_hash": response.json()["revision_hash"],
    }


@pytest.mark.parametrize("sale_format,units", [("unit", 1), ("blister", 10), ("pack", 30)])
async def test_options_quote_command_status_and_exact_replay(
    client: AsyncClient, db_session: AsyncSession, sale_format: str, units: int
) -> None:
    original = await source(db_session)
    before = await facts(db_session)
    async with refund_actor(db_session):
        options = await client.get(f"{path(original.sale_id)}/options")
        assert options.status_code == 200
        assert options.headers["cache-control"] == "private, no-store"
        assert options.json()["lines"][0]["remaining_base_units"] == 60
        assert_no_cost_fields(options.json())
        own_shift = await client.get(
            "/api/v1/shifts/current", params={"register_id": str(original.register_id)}
        )
        assert own_shift.status_code == 200
        assert own_shift.json()["id"] == str(original.shift_id)
        body = await request(client, original, sale_format)
        assert await facts(db_session) == before
        response = await client.post(f"{path(original.sale_id)}/complete", json=body)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert payload["operation_id"] == body["operation_id"]
        assert payload["total_amount"] == body["expected_total_amount"]
        assert payload["receipt"]["lines"][0]["base_units"] == units
        assert payload["receipt"]["lines"][0]["sale_format"] == sale_format
        assert_no_cost_fields(payload)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        after = await facts(db_session)
        assert len(after["sale"]) == len(before["sale"]) + 1
        assert len(after["sale_payment"]) == len(before["sale_payment"]) + 1
        assert len(after["pos_physical_return_command"]) == 1
        assert len(after["pos_physical_cash_refund"]) == 1
        assert (
            await client.post(f"{path(original.sale_id)}/complete", json=body)
        ).json() == payload
        status = await client.get(f"{path(original.sale_id)}/operations/{body['operation_id']}")
        assert status.status_code == 200 and status.json() == payload
        updated = await client.get(f"{path(original.sale_id)}/options")
        assert updated.json()["lines"][0]["remaining_base_units"] == 60 - units
        assert await facts(db_session) == after


async def test_stale_quote_is_rejected_even_when_payout_amount_is_unchanged(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await source(db_session)
    async with refund_actor(db_session):
        stale = await request(client, original)
        await refund(db_session, original.sale_id, original.lines[0].id, original.shift_id)
        before = await facts(db_session)
        response = await client.post(f"{path(original.sale_id)}/complete", json=stale)
        assert response.status_code == 409
        assert await facts(db_session) == before
        fresh = await request(client, original)
        assert fresh["expected_total_amount"] == stale["expected_total_amount"]
        assert fresh["expected_revision_hash"] != stale["expected_revision_hash"]
        assert (
            await client.post(f"{path(original.sale_id)}/complete", json=fresh)
        ).status_code == 200


async def test_reason_policy_change_and_reviewed_amount_denials_are_atomic(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await source(db_session)
    async with refund_actor(db_session):
        body = await request(client, original)
        repository = FoundationRepository(db_session)
        settings = await repository.get_settings(original.tenant_id)
        assert settings is not None
        await repository.update_settings(settings, refund_reason_mode="required_with_text")
        before = await facts(db_session)
        assert (
            await client.post(f"{path(original.sale_id)}/complete", json=body)
        ).status_code == 422
        assert await facts(db_session) == before
        options = await client.get(f"{path(original.sale_id)}/options")
        assert options.json()["reason_mode"] == "required_with_text"
        body.update(reason_code="other", comment=" Ошибка количества ")
        wrong_amount = {**body, "expected_total_amount": "0.01"}
        assert (
            await client.post(f"{path(original.sale_id)}/complete", json=wrong_amount)
        ).status_code == 409
        assert await facts(db_session) == before
        response = await client.post(f"{path(original.sale_id)}/complete", json=body)
        assert response.status_code == 200
        assert "comment" not in response.json()
        assert "reason_code" not in response.json()


@pytest.mark.parametrize(
    "mutation", ["zero", "fraction", "boolean", "duplicate", "unknown", "excess", "cost"]
)
async def test_invalid_preview_selection_has_no_side_effects(
    client: AsyncClient, db_session: AsyncSession, mutation: str
) -> None:
    original = await source(db_session)
    selected = items(original)
    if mutation == "zero":
        selected[0]["quantity"] = 0
    elif mutation == "fraction":
        selected[0]["quantity"] = 1.5
    elif mutation == "boolean":
        selected[0]["quantity"] = True
    elif mutation == "duplicate":
        selected.append(dict(selected[0]))
    elif mutation == "unknown":
        selected[0]["line_id"] = str(uuid4())
    elif mutation == "excess":
        selected[0]["quantity"] = 61
    else:
        selected[0]["total_cost_amount"] = "0.01"
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.post(f"{path(original.sale_id)}/quote", json={"items": selected})
        assert response.status_code == 422
    assert await facts(db_session) == before


@pytest.mark.parametrize("failure", ["permission", "branch", "claimed_actor", "claimed_tenant"])
async def test_all_public_actions_respect_actual_actor_and_branch_scope(
    client: AsyncClient, db_session: AsyncSession, failure: str
) -> None:
    original = await source(db_session)
    async with refund_actor(db_session) as actor:
        body = await request(client, original)
        if failure == "permission":
            actor.permissions = {"pos.sell"}
            actor.permission_scopes = {"pos.sell": frozenset({original.branch_id})}
        elif failure == "branch":
            actor.permission_scopes = {"pos.refund": frozenset({uuid4()})}
        elif failure == "claimed_actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        before = await facts(db_session)
        responses = (
            await client.get(f"{path(original.sale_id)}/options"),
            await client.post(f"{path(original.sale_id)}/quote", json={"items": items(original)}),
            await client.post(f"{path(original.sale_id)}/complete", json=body),
        )
        assert all(response.status_code == 403 for response in responses)
        assert await facts(db_session) == before


async def test_closed_shift_replay_is_preserved_but_new_payout_is_denied(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await source(db_session)
    async with refund_actor(db_session):
        body = await request(client, original)
        completed = await client.post(f"{path(original.sale_id)}/complete", json=body)
        assert completed.status_code == 200
        await close_shift(db_session, original.shift_id)
        before = await facts(db_session)
        assert (
            await client.post(f"{path(original.sale_id)}/complete", json=body)
        ).json() == completed.json()
        fresh = await request(client, original)
        assert (
            await client.post(f"{path(original.sale_id)}/complete", json=fresh)
        ).status_code == 422
        assert await facts(db_session) == before


async def test_readonly_tenant_can_preview_but_cannot_issue_cash(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    original = await source(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"), {"id": original.tenant_id}
        )
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    async with refund_actor(db_session):
        options = await client.get(f"{path(original.sale_id)}/options")
        assert options.status_code == 200
        body = await request(client, original)
        response = await client.post(f"{path(original.sale_id)}/complete", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "business_rule_violation"
    assert await facts(db_session) == before
