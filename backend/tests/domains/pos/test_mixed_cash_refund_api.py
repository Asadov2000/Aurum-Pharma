"""Mixed refund HTTP flow preserves both inventories, RLS and exact payout replay."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.foundation.repository import FoundationRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_cash_refund import facts, refund
from tests.domains.pos.test_mixed_return_journal import MixedSource, mixed_source
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields, refund_actor
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def path(parent: UUID) -> str:
    return f"/api/v1/sales/{parent}/mixed-cash-refunds"


async def source(session: AsyncSession, *, fractional: bool = False) -> MixedSource:
    original = await mixed_source(session, fractional_legacy=fractional)
    repository = FoundationRepository(session)
    settings = await repository.get_settings(original.original.tenant_id)
    assert settings is not None
    await repository.update_settings(settings, refund_reason_mode="off")
    return original


def items(original: MixedSource, sale_format: str = "unit") -> list[dict[str, object]]:
    return [
        {
            "inventory_kind": "legacy_pack",
            "line_id": str(original.legacy_id),
            "quantity": "0.001",
        },
        {
            "inventory_kind": "physical_unit",
            "line_id": str(original.physical_id),
            "sale_format": sale_format,
            "quantity": 1,
        },
    ]


async def request(
    client: AsyncClient, original: MixedSource, sale_format: str = "unit"
) -> dict[str, object]:
    selected = items(original, sale_format)
    response = await client.post(
        f"{path(original.original.sale_id)}/quote", json={"items": selected}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert_no_cost_fields(response.json())
    return {
        "operation_id": str(uuid4()),
        "shift_id": str(original.original.shift_id),
        "items": selected,
        "expected_total_amount": response.json()["total_amount"],
        "expected_revision_hash": response.json()["revision_hash"],
    }


@pytest.mark.parametrize("sale_format,units", [("unit", 1), ("blister", 10), ("pack", 30)])
async def test_options_quote_complete_and_recovery_preserve_both_kinds_and_exact_replay(
    client: AsyncClient, db_session: AsyncSession, sale_format: str, units: int
) -> None:
    original = await source(db_session)
    endpoint = path(original.original.sale_id)
    before = await facts(db_session)
    async with refund_actor(db_session):
        options = await client.get(f"{endpoint}/options")
        assert options.status_code == 200
        assert options.headers["cache-control"] == "private, no-store"
        assert [line["inventory_kind"] for line in options.json()["lines"]] == [
            "legacy_pack",
            "physical_unit",
        ]
        assert_no_cost_fields(options.json())
        body = await request(client, original, sale_format)
        assert await facts(db_session) == before
        response = await client.post(f"{endpoint}/complete", json=body)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert payload["operation_id"] == body["operation_id"]
        assert payload["total_amount"] == body["expected_total_amount"]
        assert payload["receipt"]["schema_version"] == 4
        assert [line["inventory_kind"] for line in payload["receipt"]["lines"]] == [
            "legacy_pack",
            "physical_unit",
        ]
        assert payload["receipt"]["lines"][1]["base_units"] == units
        assert_no_cost_fields(payload)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        after = await facts(db_session)
        assert len(after["sale"]) == len(before["sale"]) + 1
        assert len(after["sale_payment"]) == len(before["sale_payment"]) + 1
        assert len(after["pos_mixed_cash_refund"]) == 1
        assert len(after["pos_mixed_return_line"]) == 2
        assert (await client.post(f"{endpoint}/complete", json=body)).json() == payload
        status = await client.get(f"{endpoint}/operations/{body['operation_id']}")
        assert status.status_code == 200 and status.json() == payload
        assert status.headers["cache-control"] == "private, no-store"
        assert await facts(db_session) == after


async def test_stale_review_is_rejected_without_second_payout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await source(db_session)
    async with refund_actor(db_session):
        stale = await request(client, original)
        await refund(db_session, original)
        before = await facts(db_session)
        response = await client.post(f"{path(original.original.sale_id)}/complete", json=stale)
        assert response.status_code == 409
        assert await facts(db_session) == before
        fresh = await request(client, original)
        assert fresh["expected_revision_hash"] != stale["expected_revision_hash"]
        assert (
            await client.post(f"{path(original.original.sale_id)}/complete", json=fresh)
        ).status_code == 200


@pytest.mark.parametrize(
    "mutation", ["zero", "fraction", "boolean", "duplicate", "unknown", "excess", "cost", "kind"]
)
async def test_invalid_selection_of_either_inventory_has_no_side_effects(
    client: AsyncClient, db_session: AsyncSession, mutation: str
) -> None:
    original = await source(db_session)
    selected = items(original)
    if mutation == "zero":
        selected[0]["quantity"] = "0.000"
    elif mutation == "fraction":
        selected[1]["quantity"] = 1.5
    elif mutation == "boolean":
        selected[0]["quantity"] = True
    elif mutation == "duplicate":
        selected.append(dict(selected[0]))
    elif mutation == "unknown":
        selected[0]["line_id"] = str(uuid4())
    elif mutation == "excess":
        selected[1]["quantity"] = 61
    elif mutation == "cost":
        selected[0]["physical_cost_amount"] = "0.01"
    else:
        selected[0]["inventory_kind"] = "physical_unit"
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.post(
            f"{path(original.original.sale_id)}/quote", json={"items": selected}
        )
        assert response.status_code == 422
    assert await facts(db_session) == before


async def test_required_reason_and_wrong_reviewed_amount_do_not_claim_stock_or_money(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await source(db_session)
    endpoint = path(original.original.sale_id)
    async with refund_actor(db_session):
        body = await request(client, original)
        repository = FoundationRepository(db_session)
        settings = await repository.get_settings(original.original.tenant_id)
        assert settings is not None
        await repository.update_settings(settings, refund_reason_mode="required_with_text")
        before = await facts(db_session)
        assert (await client.post(f"{endpoint}/complete", json=body)).status_code == 422
        body.update(reason_code="other", comment=" Ошибка количества ")
        wrong_amount = {**body, "expected_total_amount": "0.01"}
        assert (await client.post(f"{endpoint}/complete", json=wrong_amount)).status_code == 409
        assert await facts(db_session) == before
        response = await client.post(f"{endpoint}/complete", json=body)
        assert response.status_code == 200
        assert "comment" not in response.json()
        assert "reason_code" not in response.json()


async def test_closed_shift_allows_exact_replay_and_read_but_denies_new_cash_payout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await source(db_session)
    endpoint = path(original.original.sale_id)
    async with refund_actor(db_session):
        body = await request(client, original)
        response = await client.post(f"{endpoint}/complete", json=body)
        assert response.status_code == 200
        await close_shift(db_session, original.original.shift_id)
        before = await facts(db_session)
        assert (await client.post(f"{endpoint}/complete", json=body)).json() == response.json()
        assert (
            await client.get(f"{endpoint}/operations/{body['operation_id']}")
        ).json() == response.json()
        fresh = await request(client, original)
        assert (await client.post(f"{endpoint}/complete", json=fresh)).status_code == 422
        assert await facts(db_session) == before


async def test_readonly_tenant_can_preview_and_recover_but_cannot_issue_cash(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    original = await source(db_session)
    result = await refund(db_session, original)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": original.original.tenant_id},
        )
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    endpoint = path(original.original.sale_id)
    async with refund_actor(db_session):
        assert (await client.get(f"{endpoint}/options")).status_code == 200
        body = await request(client, original)
        assert (await client.post(f"{endpoint}/complete", json=body)).status_code == 422
        recovered = await client.get(f"{endpoint}/operations/{result.operation_id}")
        assert recovered.status_code == 200
        assert recovered.json()["receipt"] == result.receipt.model_dump(mode="json")
        assert_no_cost_fields(recovered.json())
    assert await facts(db_session) == before


@pytest.mark.parametrize("failure", ["permission", "branch", "claimed_actor", "claimed_tenant"])
async def test_claimed_identity_or_scope_cannot_authorize_preview_payout_or_recovery(
    client: AsyncClient, db_session: AsyncSession, failure: str
) -> None:
    original = await source(db_session)
    endpoint = path(original.original.sale_id)
    before = await facts(db_session)
    async with refund_actor(db_session) as actor:
        body = await request(client, original)
        if failure == "permission":
            actor.permissions = {"sales.view.tenant"}
        elif failure == "branch":
            actor.permission_scopes = {"pos.refund": frozenset({uuid4()})}
        elif failure == "claimed_actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        responses = [
            await client.get(f"{endpoint}/options"),
            await client.post(f"{endpoint}/quote", json={"items": items(original)}),
            await client.post(f"{endpoint}/complete", json=body),
            await client.get(f"{endpoint}/operations/{uuid4()}"),
        ]
        assert all(response.status_code == 403 for response in responses)
    assert await facts(db_session) == before


async def test_real_foreign_tenant_cannot_read_original_or_recover_refund(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    original = await source(db_session)
    result = await refund(db_session, original)
    context = runtime(db_session)
    before = await facts(db_session)
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            foreign = await seed_ledger_context(support)
    foreign_actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    await set_receiving_context(db_session, foreign, foreign_actor)
    endpoint = path(original.original.sale_id)
    async with refund_actor(db_session) as actor:
        actor.user_id = foreign_actor
        actor.tenant_id = foreign.tenant_id
        actor.permission_scopes = {"pos.refund": frozenset({foreign.branch_id})}
        assert (await client.get(f"{endpoint}/options")).status_code == 404
        assert (
            await client.post(f"{endpoint}/quote", json={"items": items(original)})
        ).status_code == 404
        assert (
            await client.get(f"{endpoint}/operations/{result.operation_id}")
        ).status_code == 404
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before
