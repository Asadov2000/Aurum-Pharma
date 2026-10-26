"""Training HTTP completion and recovery must never reach real payment or stock."""

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser, get_db
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import RefundCreate
from app.domains.pos.service import POSService
from app.main import app
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_training_checkout import facts, unchanged_effects
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import (
    PhysicalCheckoutSource,
    checkout_source,
)
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def training_path(sale_id: UUID) -> str:
    return f"/api/v1/sales/{sale_id}/training"


async def source_for(
    session: AsyncSession, *, mixed: bool = False, training: bool = True
) -> PhysicalCheckoutSource:
    source = await checkout_source(session)
    if mixed:
        await legacy_with_stock(session, source.sale)
    await put_physical(session, source.sale.id, sale_format="unit", quantity=3)
    await session.execute(
        text("UPDATE sale SET is_test=:training WHERE id=:sale"),
        {"training": training, "sale": source.sale.id},
    )
    await session.execute(
        text(
            'UPDATE tenant_settings SET pos_payment_methods=\'["cash","card","qr"]\'::jsonb, '
            "pos_mixed_payment_enabled=true WHERE tenant_id=:tenant"
        ),
        {"tenant": source.sale.tenant_id},
    )
    return source


async def request_for(
    client: AsyncClient, sale_id: UUID, method: str = "cash"
) -> dict[str, object]:
    response = await client.get(f"/api/v1/sales/{sale_id}/physical-cart/quote")
    assert response.status_code == 200
    quote = response.json()
    total = Decimal(quote["quote"]["total_amount"])
    parts = [{"method": method, "amount": format(total, ".2f")}]
    cash = total if method == "cash" else Decimal("0.00")
    if method == "split":
        cash = Decimal("0.01")
        parts = [
            {"method": "cash", "amount": "0.01"},
            {"method": "card", "amount": "0.01"},
            {"method": "qr", "amount": format(total - Decimal("0.02"), ".2f")},
        ]
    return {
        "schema_version": 1,
        "operation_id": str(uuid4()),
        "sale_id": str(sale_id),
        "expected_revision_hash": quote["revision_hash"],
        "expected_total_amount": format(total, ".2f"),
        "simulated_payments": parts,
        "cash_tendered": format(cash + 1 if cash else Decimal("0.00"), ".2f"),
    }


async def real_effects(session: AsyncSession) -> dict[str, list[object]]:
    # Sale completion, one receipt number and its audit record are intentional.
    result = unchanged_effects(await facts(session))
    financial = await session.execute(
        text(
            "SELECT count(*),coalesce(sum(total_amount),0) FROM sale "
            "WHERE tenant_id=:tenant AND status='completed' AND NOT is_test"
        ),
        {"tenant": runtime(session).stock.tenant_id},
    )
    result["real_sales_summary"] = list(financial.one())
    return result


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("method", ["cash", "card", "qr", "split"])
async def test_training_http_completes_once_and_recovers_without_real_effects(
    client: AsyncClient, db_session: AsyncSession, mixed: bool, method: str
) -> None:
    source = await source_for(db_session, mixed=mixed)
    sale_id = source.sale.id
    path = training_path(sale_id)
    async with cart_actor(db_session) as actor:
        body = await request_for(client, sale_id, method)
        status_path = f"{path}/operations/{body['operation_id']}"
        missing = await client.get(status_path)
        assert missing.status_code == 200 and missing.json()["state"] == "not_found"
        assert missing.headers["cache-control"] == "private, no-store"
        before = await real_effects(db_session)
        completed = await client.post(f"{path}/complete", json=body)
        assert completed.status_code == 200
        assert completed.headers["cache-control"] == "private, no-store"
        result = completed.json()
        assert result["document_kind"] == "training_checkout" and result["is_test"] is True
        assert result["operation_id"] == body["operation_id"]
        assert result["sale_id"] == str(sale_id)
        assert result["receipt_number"].startswith("TEST-")
        assert result["receipt"]["document_kind"] == "training_sale_receipt"
        assert result["receipt"]["is_test"] is True
        assert len(result["receipt"]["lines"]) == (2 if mixed else 1)
        assert result["receipt"]["simulated_payments"] == body["simulated_payments"]
        assert result["total_amount"] == body["expected_total_amount"]
        assert await real_effects(db_session) == before
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM pos_training_checkout WHERE sale_id=:sale"),
                {"sale": sale_id},
            )
            == 1
        )
        posted = await facts(db_session)
        assert (await client.post(f"{path}/complete", json=body)).json() == result
        restored = await client.get(status_path)
        assert restored.status_code == 200 and restored.json()["result"] == result
        assert restored.headers["cache-control"] == "private, no-store"
        unknown = await client.get(f"{path}/operations/{uuid4()}")
        assert unknown.status_code == 200 and unknown.json()["state"] == "not_found"
        assert await facts(db_session) == posted
        await _assert_receipt_compatibility(client, source, actor, result["receipt_number"], mixed)
        if mixed and method == "split":
            await _closed_shift_recovery(client, db_session, source, body, result)


async def _assert_receipt_compatibility(
    client: AsyncClient,
    source: PhysicalCheckoutSource,
    actor: CurrentUser,
    receipt_number: str,
    mixed: bool,
) -> None:
    actor.permissions.add("sales.view.own")
    actor.permission_scopes["sales.view.own"] = frozenset({source.sale.branch_id})
    details = await client.get(f"/api/v1/sales/{source.sale.id}")
    assert details.status_code == 200
    assert details.json()["status"] == "completed" and details.json()["is_test"] is True
    assert details.json()["payments"] == []
    assert len(details.json()["mixed_items"]) == (2 if mixed else 1)
    assert details.json()["items"] == [] and details.json()["physical_items"] == []
    receipt = await client.get(f"/api/v1/sales/{source.sale.id}/receipt")
    assert receipt.status_code == 200
    assert receipt.json()["receipt_number"] == receipt_number
    assert Decimal(receipt.json()["total"]) == Decimal(details.json()["total_amount"])


async def _closed_shift_recovery(
    client: AsyncClient,
    session: AsyncSession,
    source: PhysicalCheckoutSource,
    body: dict[str, object],
    result: dict[str, object],
) -> None:
    context = runtime(session)
    service = POSService(POSRepository(session))
    fresh = await service.create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=source.sale.register_id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert fresh.id != source.sale.id and fresh.status == "draft"
    closed = await service.close_shift(
        shift_id=source.sale.shift_id,
        closing_cash_actual=Decimal("0.00"),
        closed_by_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert closed.status == "closed"
    saved = await facts(session)
    path = training_path(source.sale.id)
    response = await client.get(f"{path}/operations/{body['operation_id']}")
    assert response.status_code == 200 and response.json()["result"] == result
    replay = await client.post(f"{path}/complete", json=body)
    assert replay.status_code == 200 and replay.json() == result
    assert await facts(session) == saved


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_training_http_requires_current_actor_and_resource_scope(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    source = await source_for(db_session)
    path = training_path(source.sale.id)
    async with cart_actor(db_session) as actor:
        body = await request_for(client, source.sale.id)
        before = await facts(db_session)
        if denial == "permission":
            actor.permissions.clear()
        elif denial == "branch":
            actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        elif denial == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        assert (await client.post(f"{path}/complete", json=body)).status_code == 403
        assert (await client.get(f"{path}/operations/{body['operation_id']}")).status_code == 403
        assert await facts(db_session) == before


@pytest.mark.parametrize(
    "change",
    [
        {"is_test": False},
        {"stock_command_id": str(uuid4())},
        {"expected_total_amount": 0.1},
        {"cash_tendered": True},
        {"operation_id": str(UUID(int=1))},
        {"simulated_payments": [{"method": "cash", "amount": "0.00"}]},
    ],
)
async def test_training_http_rejects_untrusted_payload_before_effects(
    client: AsyncClient, db_session: AsyncSession, change: dict[str, object]
) -> None:
    source = await source_for(db_session)
    async with cart_actor(db_session):
        body = await request_for(client, source.sale.id)
        before = await facts(db_session)
        response = await client.post(
            f"{training_path(source.sale.id)}/complete", json={**body, **change}
        )
        assert response.status_code == 422
        assert await facts(db_session) == before


async def test_training_http_rejects_real_sale_stale_quote_and_path_alias(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await source_for(db_session, training=False)
    async with cart_actor(db_session):
        body = await request_for(client, source.sale.id)
        before = await facts(db_session)
        assert (
            await client.post(f"{training_path(source.sale.id)}/complete", json=body)
        ).status_code == 422
        assert await facts(db_session) == before
        await db_session.execute(
            text("UPDATE sale SET is_test=true WHERE id=:sale"), {"sale": source.sale.id}
        )
        before = await facts(db_session)
        assert (
            await client.post(
                f"{training_path(source.sale.id)}/complete",
                json={**body, "expected_revision_hash": "f" * 64},
            )
        ).status_code == 409
        assert (
            await client.post(f"{training_path(uuid4())}/complete", json=body)
        ).status_code == 422
        assert await facts(db_session) == before


async def test_training_http_readonly_can_recover_but_cannot_post(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await source_for(db_session, mixed=True)
    async with cart_actor(db_session):
        body = await request_for(client, source.sale.id)
        completed = await client.post(f"{training_path(source.sale.id)}/complete", json=body)
        assert completed.status_code == 200
        result = completed.json()
    await db_session.commit()
    context = runtime(db_session)
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with cart_actor(db_session):
        before = await facts(db_session)
        path = training_path(source.sale.id)
        restored = await client.get(f"{path}/operations/{body['operation_id']}")
        assert restored.status_code == 200 and restored.json()["result"] == result
        assert (await client.post(f"{path}/complete", json=body)).status_code == 422
        assert await facts(db_session) == before


async def test_training_http_commit_failure_never_returns_saved_success(
    db_session: AsyncSession,
) -> None:
    source = await source_for(db_session)
    sale_id = source.sale.id

    async def failing_commit() -> AsyncIterator[AsyncSession]:
        async with db_session.begin_nested():
            yield db_session
            raise RuntimeError("Simulated training commit failure")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with cart_actor(db_session):
            body = await request_for(client, sale_id)
            before = await facts(db_session)
            app.dependency_overrides[get_db] = failing_commit
            response = await client.post(f"{training_path(sale_id)}/complete", json=body)
            assert response.status_code == 500
            assert await facts(db_session) == before
            assert (
                await db_session.scalar(
                    text("SELECT count(*) FROM pos_training_checkout WHERE sale_id=:sale"),
                    {"sale": sale_id},
                )
                == 0
            )


async def test_training_mixed_legacy_refund_cannot_create_real_payment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await source_for(db_session, mixed=True)
    async with cart_actor(db_session) as actor:
        body = await request_for(client, source.sale.id)
        response = await client.post(f"{training_path(source.sale.id)}/complete", json=body)
        assert response.status_code == 200
        legacy = await db_session.execute(
            text("SELECT id,qty FROM sale_item WHERE sale_id=:sale"), {"sale": source.sale.id}
        )
        item_id, quantity = legacy.one()
        actor.permissions.add("pos.refund")
        actor.permission_scopes["pos.refund"] = frozenset({source.sale.branch_id})
        before = await facts(db_session)
        payload = RefundCreate.model_validate(
            {
                "operation_id": str(uuid4()),
                "items": [{"sale_item_id": str(item_id), "qty": str(quantity)}],
                "reason": "customer_cancelled",
                "comment": "Training refund is not supported",
            }
        )
        denied = await client.post(
            f"/api/v1/sales/{source.sale.id}/refund", json=payload.model_dump(mode="json")
        )
        assert denied.status_code == 422
        assert await facts(db_session) == before
