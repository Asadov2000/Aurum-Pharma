"""A physical pack supports thirty distinct committed public cash sales, never thirty-one."""

from decimal import Decimal
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import remaining
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_checkout import facts
from tests.domains.pos.test_physical_cash_checkout_api import cash_path, quoted_request
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def prepare_unit_cash(
    client: AsyncClient, session: AsyncSession, sale_id: UUID
) -> CashConfirmationRequestV2:
    item = await client.put(
        f"/api/v1/sales/{sale_id}/physical-cart/items/{uuid4()}",
        json={
            "catalog_id": str(runtime(session).stock.catalog_id),
            "position": 1,
            "sale_format": "unit",
            "quantity": 1,
            "discount_amount": "0.00",
        },
    )
    assert item.status_code == 200
    request = await quoted_request(client, sale_id)
    response = await client.post(
        f"{cash_path(sale_id)}/intent", json=request.model_dump(mode="json")
    )
    assert response.status_code == 200
    return request


async def assert_exhausted_pack(db_session: AsyncSession, tenant_id: UUID, lot_id: UUID) -> None:
    payments = (
        await db_session.execute(
            text("SELECT count(*),sum(amount) FROM sale_payment WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
    ).one()
    assert tuple(payments) == (30, Decimal("60.00"))
    movements = (
        await db_session.execute(
            text(
                "SELECT sum(units_taken),sum(cost_amount),min(cost_unit_offset),"
                "max(cost_unit_offset),count(DISTINCT cost_unit_offset) "
                "FROM unit_stock_movement_v2 WHERE lot_id=:lot AND units_taken>0"
            ),
            {"lot": lot_id},
        )
    ).one()
    assert tuple(movements) == (30, Decimal("10.00"), 0, 29, 30)
    packs = (
        await db_session.execute(
            text(
                "SELECT count(*),sum(remaining_units) FROM unit_stock_opened_pack_v2 "
                "WHERE lot_id=:lot"
            ),
            {"lot": lot_id},
        )
    ).one()
    assert tuple(packs) == (1, 0)


async def test_thirty_public_sales_exhaust_one_pack_without_extra_payment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await checkout_source(db_session, quantity=1)
    context = runtime(db_session)
    register_id = source.sale.register_id
    first_sale_id = source.sale.id
    pos = POSService(POSRepository(db_session))

    async def new_sale() -> UUID:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        sale = await pos.create_sale(
            tenant_id=context.stock.tenant_id,
            register_id=register_id,
            cashier_user_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
        )
        return sale.id

    receipts: set[str] = set()
    sale_ids: set[UUID] = set()
    operations: set[UUID] = set()
    async with cart_actor(db_session):
        # An early saved intent is not a stock reservation and cannot oversell later.
        pending_id = await new_sale()
        pending = await prepare_unit_cash(client, db_session, pending_id)
        for index in range(30):
            sale_id = first_sale_id if index == 0 else await new_sale()
            request = await prepare_unit_cash(client, db_session, sale_id)
            body = request.model_dump(mode="json")
            response = await client.post(f"{cash_path(sale_id)}/complete", json=body)
            assert response.status_code == 200
            result = response.json()
            # The test's DB override has no request teardown commit. Persist every
            # checkout separately so deferred guards and later retries see real history.
            await db_session.commit()
            await set_receiving_context(db_session, context.stock, context.actor_id)
            assert result["lines"][0]["base_units"] == 1
            assert result["lines"][0]["sale_format"] == "unit"
            assert Decimal(result["total_amount"]) == Decimal("2.00")
            assert len(result["payments"]) == 1
            assert Decimal(result["payments"][0]["amount"]) == Decimal("2.00")
            receipts.add(result["receipt_number"])
            sale_ids.add(sale_id)
            operations.add(request.operation_id)
            assert (await client.post(f"{cash_path(sale_id)}/complete", json=body)).json() == result
            await set_receiving_context(db_session, context.stock, context.actor_id)
            assert await remaining(db_session, source.stock) == 29 - index
            receipt = await db_session.scalar(
                text("SELECT receipt_snapshot FROM sale WHERE id=:sale"), {"sale": sale_id}
            )
            assert receipt["schema_version"] == 3
            assert receipt["receipt_number"] == result["receipt_number"]

        assert len(receipts) == len(sale_ids) == len(operations) == 30
        before = await facts(db_session)
        denied = await client.post(
            f"{cash_path(pending_id)}/complete", json=pending.model_dump(mode="json")
        )
        assert denied.status_code in {409, 422}
        assert "Insufficient compatible physical stock" in denied.text
        assert await facts(db_session) == before
        assert (
            await db_session.scalar(
                text("SELECT status FROM sale WHERE id=:sale"), {"sale": pending_id}
            )
            == "draft"
        )
        await assert_exhausted_pack(db_session, context.stock.tenant_id, source.stock.lot_id)
