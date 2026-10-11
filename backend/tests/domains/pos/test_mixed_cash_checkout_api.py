"""Public mixed cash checkout binds both inventories to one real cashier operation."""

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import get_db
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalResultLineV3
from app.main import app
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense, remaining
from tests.domains.pos.test_mixed_cash_checkout import facts
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_checkout_api import quoted_request
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def cash_path(sale_id: UUID) -> str:
    return f"/api/v1/sales/{sale_id}/mixed-cash"


def assert_mixed_result(
    result: MixedCheckoutResultV4,
    *,
    sale_id: UUID,
    operation_id: UUID,
    legacy_id: UUID,
    physical_id: UUID,
    sale_format: SaleFormatV2,
    quantity: int,
    units: int,
    total: Decimal,
) -> None:
    assert result.schema_version == 4 and result.sale_id == sale_id
    assert result.operation_id == result.stock_command_id == operation_id
    assert tuple(line.id for line in result.lines) == (legacy_id, physical_id)
    assert tuple(line.inventory_kind for line in result.lines) == ("legacy_pack", "physical_unit")
    assert result.lines[0].quantity == Decimal("0.001")
    physical = result.lines[1]
    assert isinstance(physical, PhysicalResultLineV3)
    assert (physical.sale_format, physical.quantity, physical.base_units) == (
        sale_format,
        quantity,
        units,
    )
    assert len(result.payments) == 1
    assert result.payments[0].method == "cash" and result.payments[0].amount == total
    assert result.total_amount == total


async def financial_facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await facts(session)
    result.pop("pos_unit_cash_intent")
    result.pop("audit_log")
    return result


@pytest.mark.parametrize(
    "sale_format,quantity,units", [("pack", 1, 30), ("blister", 2, 20), ("unit", 5, 5)]
)
async def test_mixed_cash_http_formats_intent_complete_replay_and_status(
    client: AsyncClient,
    db_session: AsyncSession,
    sale_format: SaleFormatV2,
    quantity: int,
    units: int,
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    item = await put_physical(
        db_session, source.sale.id, position=3, sale_format=sale_format, quantity=quantity
    )
    path = cash_path(source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        body = request.model_dump(mode="json")
        status_path = f"{path}/operations/{request.operation_id}"
        absent = await client.get(status_path)
        assert absent.status_code == 200 and absent.json()["state"] == "not_found"
        assert absent.headers["cache-control"] == "private, no-store"
        before = await remaining(db_session, source.stock)
        unposted = await financial_facts(db_session)
        intent = await client.post(f"{path}/intent", json=body)
        assert intent.status_code == 200 and intent.json()["state"] == "saved_draft"
        assert intent.headers["cache-control"] == "private, no-store"
        assert await financial_facts(db_session) == unposted
        assert await remaining(db_session, source.stock) == before
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM sale_payment WHERE sale_id=:sale"),
                {"sale": source.sale.id},
            )
            == 0
        )
        assert (await client.post(f"{path}/intent", json=body)).json() == intent.json()
        saved = await client.get(status_path)
        assert saved.json()["state"] == "saved_draft"
        assert saved.json()["saved_intent"] == intent.json()
        assert "result" not in saved.json()
        completed = await client.post(f"{path}/complete", json=body)
        assert completed.status_code == 200
        assert completed.headers["cache-control"] == "private, no-store"
        result = completed.json()
        assert_mixed_result(
            MixedCheckoutResultV4.model_validate(result),
            sale_id=source.sale.id,
            operation_id=request.operation_id,
            legacy_id=legacy.id,
            physical_id=item.id,
            sale_format=sale_format,
            quantity=quantity,
            units=units,
            total=request.expected_total_amount,
        )
        assert await remaining(db_session, source.stock) == before - units
        posted = await facts(db_session)
        assert (await client.post(f"{path}/complete", json=body)).json() == result
        status = await client.get(status_path)
        assert status.status_code == 200 and status.json()["state"] == "completed"
        assert status.json()["result"] == result
        assert status.json()["saved_intent"] == intent.json()
        assert status.headers["cache-control"] == "private, no-store"
        assert await facts(db_session) == posted
        assert (
            await db_session.scalar(
                text("SELECT qty_remaining FROM batch WHERE id=:id"), {"id": legacy.batch_id}
            )
            == Decimal("0.000")
        )
        receipt = await db_session.scalar(
            text("SELECT receipt_snapshot FROM sale WHERE id=:sale"),
            {"sale": source.sale.id},
        )
        assert receipt["schema_version"] == 4


async def test_completed_mixed_cash_status_and_replay_do_not_require_open_shift_or_quote(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    path = cash_path(source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        body = request.model_dump(mode="json")
        assert (await client.post(f"{path}/intent", json=body)).status_code == 200
        original = await client.post(f"{path}/complete", json=body)
        assert original.status_code == 200
        await db_session.execute(
            text("UPDATE shift SET status='closed',closed_at=now() WHERE id=:id"),
            {"id": source.sale.shift_id},
        )
        assert (
            await client.get(f"/api/v1/sales/{source.sale.id}/physical-cart/quote")
        ).status_code == 422
        await db_session.execute(
            text(
                "UPDATE tenant_catalog SET brand_name='Later name',base_price=999.00 "
                "WHERE id IN (:legacy,:physical)"
            ),
            {"legacy": legacy.catalog_id, "physical": runtime(db_session).stock.catalog_id},
        )
        before = await facts(db_session)
        status = await client.get(f"{path}/operations/{request.operation_id}")
        assert status.status_code == 200 and status.json()["result"] == original.json()
        assert (await client.post(f"{path}/complete", json=body)).json() == original.json()
        assert await facts(db_session) == before


async def test_mixed_cash_commit_failure_never_returns_success_and_rolls_back_posting(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    context = runtime(db_session)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        async with cart_actor(db_session):
            request = await quoted_request(client, source.sale.id)
            body = request.model_dump(mode="json")
            path = cash_path(source.sale.id)
            assert (await client.post(f"{path}/intent", json=body)).status_code == 200
            await db_session.commit()
            await set_receiving_context(db_session, context.stock, context.actor_id)
            before = await facts(db_session)

            async def fail_before_commit() -> AsyncIterator[AsyncSession]:
                try:
                    yield db_session
                    # Fail the real transaction after the handler produced its result.
                    # Function-scoped dependencies must finish before sending HTTP 200.
                    await db_session.execute(text("SELECT 1/0"))
                    await db_session.commit()
                except BaseException:
                    await db_session.rollback()
                    raise

            app.dependency_overrides[get_db] = fail_before_commit
            response = await client.post(f"{path}/complete", json=body)
            assert response.status_code == 500
    async with context.factory() as fresh:
        fresh.info["receiving_runtime"] = context
        await set_receiving_context(fresh, context.stock, context.actor_id)
        assert await facts(fresh) == before


async def test_mixed_cash_uuid_changed_payload_and_path_mismatch_are_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    path = cash_path(source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        body = request.model_dump(mode="json")
        for endpoint in ("intent", "complete"):
            mismatch = await client.post(
                f"{path}/{endpoint}", json=body | {"sale_id": str(uuid4())}
            )
            assert mismatch.status_code == 422
        assert (await client.post(f"{path}/intent", json=body)).status_code == 200
        before = await facts(db_session)
        changed = body | {"cash_tendered": str(request.cash_tendered + 1)}
        for endpoint in ("intent", "complete"):
            assert (await client.post(f"{path}/{endpoint}", json=changed)).status_code == 409
        assert await facts(db_session) == before


@pytest.mark.parametrize(
    "change",
    [
        {"operation_id": "00000000-0000-1000-8000-000000000000"},
        {"expected_revision_hash": "invalid"},
        {"expected_total_amount": 1.5},
        {"cash_tendered": True},
        {"cash_tendered": "0.00"},
        {"currency": "USD"},
        {"tenant_id": str(uuid4())},
        {"cashier_id": str(uuid4())},
    ],
)
async def test_mixed_cash_http_rejects_untrusted_confirmation_payload_without_side_effects(
    client: AsyncClient, db_session: AsyncSession, change: dict[str, object]
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    path = cash_path(source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        before = await facts(db_session)
        for endpoint in ("intent", "complete"):
            response = await client.post(
                f"{path}/{endpoint}", json=request.model_dump(mode="json") | change
            )
            assert response.status_code == 422
        assert await facts(db_session) == before


async def test_mixed_cash_status_other_sale_operation_is_indistinguishable_from_unknown(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    other_source = await checkout_source(db_session)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        assert (
            await client.post(
                f"{cash_path(source.sale.id)}/intent", json=request.model_dump(mode="json")
            )
        ).status_code == 200
        before = await facts(db_session)
        response = await client.get(
            f"{cash_path(other_source.sale.id)}/operations/{request.operation_id}"
        )
        assert response.status_code == 200
        assert response.json() == {
            "state": "not_found",
            "operation_id": str(request.operation_id),
            "sale_id": str(other_source.sale.id),
        }
        assert await facts(db_session) == before


async def test_mixed_cash_status_does_not_expose_physical_only_confirmation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        saved = await client.post(
            f"/api/v1/sales/{source.sale.id}/physical-cash/intent",
            json=request.model_dump(mode="json"),
        )
        assert saved.status_code == 200 and saved.json()["state"] == "saved_draft"
        before = await facts(db_session)
        response = await client.get(
            f"{cash_path(source.sale.id)}/operations/{request.operation_id}"
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json() == {
            "state": "not_found",
            "operation_id": str(request.operation_id),
            "sale_id": str(source.sale.id),
        }
        assert await facts(db_session) == before


async def test_mixed_cash_intent_rejects_stale_quote_without_saving_confirmation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id, sale_format="pack")
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        await db_session.execute(
            text("UPDATE tenant_catalog SET base_price=999.00 WHERE id=:id"),
            {"id": runtime(db_session).stock.catalog_id},
        )
        before = await facts(db_session)
        response = await client.post(
            f"{cash_path(source.sale.id)}/intent", json=request.model_dump(mode="json")
        )
        assert response.status_code == 409
        assert await facts(db_session) == before


async def test_public_mixed_cash_intent_rejects_physical_only_cart_without_side_effects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        before = await facts(db_session)
        response = await client.post(
            f"{cash_path(source.sale.id)}/intent", json=request.model_dump(mode="json")
        )
        assert response.status_code == 409
        assert await facts(db_session) == before


@pytest.mark.parametrize(
    "failure", ["stale_quote", "physical_shortage", "legacy_shortage", "missing_intent"]
)
async def test_failed_mixed_cash_http_completion_rolls_back_all_financial_effects(
    client: AsyncClient, db_session: AsyncSession, failure: str
) -> None:
    source = await checkout_source(db_session, quantity=1)
    legacy = await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id, sale_format="pack")
    path = cash_path(source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        body = request.model_dump(mode="json")
        if failure != "missing_intent":
            assert (await client.post(f"{path}/intent", json=body)).status_code == 200
        if failure == "stale_quote":
            await db_session.execute(
                text("UPDATE tenant_catalog SET base_price=999.00 WHERE id=:id"),
                {"id": runtime(db_session).stock.catalog_id},
            )
        elif failure == "physical_shortage":
            await dispense(db_session, source.stock, (cart_line(source.stock, "pack", 1),))
        elif failure == "legacy_shortage":
            await InventoryRepository(db_session).insert_movement(
                tenant_id=source.sale.tenant_id,
                batch_id=legacy.batch_id,
                movement_type="write_off",
                qty_delta=-legacy.qty,
            )
        before = await facts(db_session)
        response = await client.post(f"{path}/complete", json=body)
        assert response.status_code in {404, 409, 422}
        assert await facts(db_session) == before


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_mixed_cash_http_all_routes_enforce_actor_and_resource_scope(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    path = cash_path(source.sale.id)
    async with cart_actor(db_session) as actor:
        request = await quoted_request(client, source.sale.id)
        body = request.model_dump(mode="json")
        if denial == "permission":
            actor.permissions.clear()
        elif denial == "branch":
            actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        elif denial == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        for endpoint in ("intent", "complete"):
            assert (await client.post(f"{path}/{endpoint}", json=body)).status_code == 403
        assert (await client.get(f"{path}/operations/{request.operation_id}")).status_code == 403


@pytest.mark.parametrize("other_tenant", [False, True])
async def test_mixed_cash_http_real_other_cashier_and_tenant_cannot_access_operation(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    other_tenant: bool,
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    path = cash_path(source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        body = request.model_dump(mode="json")
        assert (await client.post(f"{path}/intent", json=body)).status_code == 200
    # Release the request's tenant advisory lock before creating another membership.
    await db_session.commit()
    stock = runtime(db_session).stock
    if other_tenant:
        async with AsyncSession(db_engine) as support:
            async with support.begin():
                await support.execute(text("SELECT set_config('app.support_session','true',true)"))
                stock = await seed_ledger_context(support)
    other_actor = await seed_receiving_actor(db_engine, maintenance_engine, stock)
    await set_receiving_context(db_session, stock, other_actor)
    async with cart_actor(db_session) as actor:
        actor.user_id = other_actor
        actor.tenant_id = stock.tenant_id
        actor.permission_scopes["pos.sell"] = None
        expected = 404 if other_tenant else 403
        for endpoint in ("intent", "complete"):
            assert (await client.post(f"{path}/{endpoint}", json=body)).status_code == expected
        assert (
            await client.get(f"{path}/operations/{request.operation_id}")
        ).status_code == expected


@pytest.mark.parametrize("completed", [False, True])
async def test_mixed_cash_http_readonly_tenant_can_reconcile_but_not_post(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine, completed: bool
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    path = cash_path(source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
        body = request.model_dump(mode="json")
        assert (await client.post(f"{path}/intent", json=body)).status_code == 200
        if completed:
            assert (await client.post(f"{path}/complete", json=body)).status_code == 200
    await db_session.commit()
    context = runtime(db_session)
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with cart_actor(db_session):
        before = await facts(db_session)
        saved = await client.get(f"{path}/operations/{request.operation_id}")
        assert saved.status_code == 200
        assert saved.json()["state"] == ("completed" if completed else "saved_draft")
        assert await facts(db_session) == before
        for endpoint in ("intent", "complete"):
            assert (await client.post(f"{path}/{endpoint}", json=body)).status_code == 422
        assert await facts(db_session) == before


async def test_mixed_cash_http_support_identity_cannot_post_or_reconcile(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    path = cash_path(source.sale.id)
    async with cart_actor(db_session):
        request = await quoted_request(client, source.sale.id)
    context = runtime(db_session)
    async with AsyncSession(db_engine) as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "SELECT set_config('app.tenant_id',:tenant,true),"
                "set_config('app.user_id',:actor,true)"
            ),
            {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
        )

        async def override_support_db() -> AsyncIterator[AsyncSession]:
            yield support

        async with cart_actor(db_session):
            app.dependency_overrides[get_db] = override_support_db
            for endpoint in ("intent", "complete"):
                assert (
                    await client.post(f"{path}/{endpoint}", json=request.model_dump(mode="json"))
                ).status_code == 403
            assert (
                await client.get(f"{path}/operations/{request.operation_id}")
            ).status_code == 403
