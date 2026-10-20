"""Real scoped HTTP electronic checkout, terminal evidence and saved recovery."""

from decimal import Decimal
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.pos.unit_payment_intent_schemas import SaveUnitPaymentIntentRequest
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_electronic_checkout import attempts, financial_facts, prepared
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def path(request: SaveUnitPaymentIntentRequest) -> str:
    return (
        f"/api/v1/sales/{request.request.sale_id}/unit-payment-plan"
        f"/intents/{request.operation_id}"
    )


@pytest.mark.parametrize("methods", [("card",), ("qr",), ("cash", "card", "qr")])
async def test_http_full_lifecycle_exact_plan_and_get_only_recovery(
    client: AsyncClient, db_session: AsyncSession, methods: tuple[str, ...]
) -> None:
    request, ids = await prepared(db_session, methods)
    url = path(request)
    body = {"attempt_ids": {method: str(value) for method, value in ids.items()}}
    async with cart_actor(db_session) as actor:
        # Tenant-wide sell still resolves to this original sale's branch/actor.
        actor.permission_scopes["pos.sell"] = None
        assert (await client.get(f"{url}/attempts")).status_code == 404
        response = await client.post(f"{url}/attempts", json=body)
        assert response.status_code == 200, response.text
        rows = response.json()
        assert response.headers["cache-control"] == "private, no-store"
        assert {row["id"] for row in rows} == {str(value) for value in ids.values()}
        expected = {part.method: part.amount for part in request.request.payments}
        assert all(Decimal(row["amount"]) == expected[row["payment_method"]] for row in rows)
        assert all(row["currency"] == "TJS" and row["status"] == "pending" for row in rows)
        before = await financial_facts(db_session)
        assert (await client.post(f"{url}/attempts", json=body)).json() == rows
        assert (await client.get(f"{url}/attempts")).json() == rows
        assert await financial_facts(db_session) == before
        assert (await client.get(f"{url}/checkout")).status_code == 404
        assert (await client.post(f"{url}/complete", json={})).status_code == 422
        for row in rows:
            attempt_url = f"{url}/attempts/{row['id']}"
            evidence = {"terminal_id": "TEST-TERMINAL", "external_reference": f"test-{row['id']}"}
            assert (await client.post(f"{attempt_url}/confirm", json=evidence)).status_code == 422
            started = await client.post(f"{attempt_url}/reconciliation", json={})
            assert started.status_code == 200, started.text
            assert started.json()["status"] == "requires_reconciliation"
            confirmed = await client.post(f"{attempt_url}/confirm", json=evidence)
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["status"] == "confirmed"
            assert (await client.post(f"{attempt_url}/confirm", json=evidence)).json() == (
                confirmed.json()
            )
            if len(rows) > 1 and row == rows[0]:
                partial = await financial_facts(db_session)
                assert (await client.post(f"{url}/complete", json={})).status_code == 422
                assert await financial_facts(db_session) == partial
            changed = {**evidence, "external_reference": "different-reference"}
            assert (await client.post(f"{attempt_url}/confirm", json=changed)).status_code == 409
        posted = await client.post(f"{url}/complete", json={})
        assert posted.status_code == 200, posted.text
        result = posted.json()
        assert result["schema_version"] == 3
        assert result["sale_id"] == str(request.request.sale_id)
        assert result["operation_id"] == str(request.operation_id)
        assert Decimal(result["total_amount"]) == request.request.expected_total_amount
        assert {p["method"]: Decimal(p["amount"]) for p in result["payments"]} == expected
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        after = await financial_facts(db_session)
        assert (await client.post(f"{url}/complete", json={})).json() == result
        await close_shift(db_session, UUID(result["shift_id"]))
        for suffix in ("checkout", "attempts"):
            recovered = await client.get(f"{url}/{suffix}")
            assert recovered.status_code == 200, recovered.text
            assert recovered.headers["cache-control"] == "private, no-store"
        assert (await client.get(f"{url}/checkout")).json() == result
        consumed = (await client.get(f"{url}/attempts")).json()
        assert all(row["status"] == "consumed" for row in consumed)
        # Completed replay and GET do not re-open a shift or create another payment.
        assert (await client.post(f"{url}/complete", json={})).json() == result
        assert await financial_facts(db_session) == after


async def test_attempt_membership_and_scope_denials_have_no_effects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, ids = await prepared(db_session)
    await attempts(db_session, request, ids)
    other, other_ids = await prepared(db_session, ("qr",))
    await attempts(db_session, other, other_ids)
    url = path(request)
    before = await financial_facts(db_session)
    async with cart_actor(db_session) as actor:
        for attempt_id in (uuid4(), *other_ids.values()):
            command_url = f"{url}/attempts/{attempt_id}"
            for suffix, body in (
                ("reconciliation", {}),
                ("confirm", {"terminal_id": "TEST", "external_reference": "test-reference"}),
                ("void", {"reason": "cashier_cancelled"}),
            ):
                assert (await client.post(f"{command_url}/{suffix}", json=body)).status_code == 404
        wrong_operation = url.replace(str(request.operation_id), str(other.operation_id))
        assert (await client.get(f"{wrong_operation}/attempts")).status_code == 404
        assert (await client.post(f"{wrong_operation}/complete", json={})).status_code == 404
        commands = [
            (f"{url}/attempts", {"attempt_ids": {"card": str(ids["card"])}}),
            (f"{url}/attempts/{ids['card']}/reconciliation", {}),
            (
                f"{url}/attempts/{ids['card']}/confirm",
                {"terminal_id": "TEST", "external_reference": "test-reference"},
            ),
            (f"{url}/attempts/{ids['card']}/void", {"reason": "cashier_cancelled"}),
            (f"{url}/complete", {}),
        ]
        actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        for command_url, body in commands:
            assert (await client.post(command_url, json=body)).status_code == 403
        assert (await client.get(f"{url}/attempts")).status_code == 403
        actor.permission_scopes["pos.sell"] = None
        saved_actor = actor.user_id
        actor.user_id = uuid4()
        assert (await client.get(f"{url}/attempts")).status_code in {403, 404}
        actor.user_id = saved_actor
        saved_tenant = actor.tenant_id
        actor.tenant_id = uuid4()
        assert (await client.get(f"{url}/attempts")).status_code == 403
        actor.tenant_id = saved_tenant
        actor.permissions = {"pos.manage_sales"}
        actor.permission_scopes = {"pos.manage_sales": None}
        assert (await client.get(f"{url}/attempts")).status_code == 403
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("bad", ["extra", "non4", "same", "missing", "duplicate", "changed"])
async def test_prepare_rejects_client_money_and_inexact_attempt_identifiers(
    client: AsyncClient, db_session: AsyncSession, bad: str
) -> None:
    request, ids = await prepared(db_session, ("cash", "card", "qr"))
    body: dict[str, object] = {"attempt_ids": {m: str(v) for m, v in ids.items()}}
    if bad == "extra":
        body["amount"] = "0.01"
    elif bad == "non4":
        body["attempt_ids"] = {"card": str(uuid5(NAMESPACE_DNS, "test")), "qr": str(ids["qr"])}
    elif bad == "same":
        body["attempt_ids"] = {"card": str(request.operation_id), "qr": str(ids["qr"])}
    elif bad == "missing":
        body["attempt_ids"] = {"card": str(ids["card"])}
    elif bad == "duplicate":
        body["attempt_ids"] = {"card": str(ids["card"]), "qr": str(ids["card"])}
    else:
        await attempts(db_session, request, ids)
        body["attempt_ids"] = {"card": str(uuid4()), "qr": str(ids["qr"])}
    before = await financial_facts(db_session)
    async with cart_actor(db_session):
        response = await client.post(f"{path(request)}/attempts", json=body)
        assert response.status_code == (409 if bad == "changed" else 422), response.text
        override = await client.post(f"{path(request)}/complete", json={"payments": []})
        assert override.status_code == 422
    assert await financial_facts(db_session) == before


async def test_void_never_releases_plan_and_uncertain_void_requires_same_branch_management(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, ids = await prepared(db_session, ("cash", "card", "qr"))
    await attempts(db_session, request, ids)
    url = path(request)
    async with cart_actor(db_session) as actor:
        pending_url = f"{url}/attempts/{ids['card']}/void"
        body = {"reason": "cashier_cancelled"}
        voided = await client.post(pending_url, json=body)
        assert voided.status_code == 200, voided.text
        assert voided.json()["status"] == "voided"
        assert (await client.post(pending_url, json=body)).json() == voided.json()
        assert (await client.post(f"{url}/cancel-binding")).status_code == 409
        uncertain_url = f"{url}/attempts/{ids['qr']}"
        assert (await client.post(f"{uncertain_url}/reconciliation", json={})).status_code == 200
        evidence = {
            "reason": "terminal_declined",
            "terminal_id": "TEST",
            "external_reference": "declined",
        }
        assert (await client.post(f"{uncertain_url}/void", json=evidence)).status_code == 403
        actor.permissions.add("pos.manage_sales")
        actor.permission_scopes["pos.manage_sales"] = frozenset({uuid4()})
        assert (await client.post(f"{uncertain_url}/void", json=evidence)).status_code == 403
        actor.permission_scopes["pos.manage_sales"] = frozenset(
            {runtime(db_session).stock.branch_id}
        )
        resolved = await client.post(f"{uncertain_url}/void", json=evidence)
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "voided"
        assert (await client.post(f"{url}/complete", json={})).status_code == 422
        assert (await client.post(f"{url}/cancel-binding")).status_code == 409


async def test_inactive_register_preserves_attempts_but_blocks_new_commands(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, ids = await prepared(db_session)
    await attempts(db_session, request, ids)
    register_id = await db_session.scalar(
        text("SELECT register_id FROM sale WHERE id=:id"), {"id": request.request.sale_id}
    )
    await db_session.execute(
        text("UPDATE register SET is_active=false WHERE id=:id"), {"id": register_id}
    )
    url = path(request)
    before = await financial_facts(db_session)
    async with cart_actor(db_session):
        assert (await client.get(f"{url}/attempts")).status_code == 200
        command_url = f"{url}/attempts/{ids['card']}/reconciliation"
        assert (await client.post(command_url, json={})).status_code == 422
        assert (await client.post(f"{url}/complete", json={})).status_code == 422
    assert await financial_facts(db_session) == before


async def test_readonly_get_attempts_does_not_change_facts(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, ids = await prepared(db_session)
    await attempts(db_session, request, ids)
    await db_session.commit()
    context = runtime(db_session)
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    url = path(request)
    before = await financial_facts(db_session)
    async with cart_actor(db_session):
        assert (await client.get(f"{url}/attempts")).status_code == 200
        for command_url, body in (
            (f"{url}/attempts", {"attempt_ids": {"card": str(ids["card"])}}),
            (f"{url}/attempts/{ids['card']}/reconciliation", {}),
            (
                f"{url}/attempts/{ids['card']}/confirm",
                {"terminal_id": "TEST", "external_reference": "x"},
            ),
            (f"{url}/attempts/{ids['card']}/void", {"reason": "cashier_cancelled"}),
            (f"{url}/complete", {}),
        ):
            assert (await client.post(command_url, json=body)).status_code == 422
    assert await financial_facts(db_session) == before


async def test_mixed_goods_remain_rejected_by_new_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request, ids = await prepared(db_session, mixed=True)
    before = await financial_facts(db_session)
    async with cart_actor(db_session):
        url = path(request)
        response = await client.post(
            f"{url}/attempts", json={"attempt_ids": {"card": str(ids["card"])}}
        )
        assert response.status_code == 422, response.text
        assert (await client.get(f"{url}/attempts")).status_code == 422
        assert (await client.post(f"{url}/complete", json={})).status_code == 422
    assert await financial_facts(db_session) == before
