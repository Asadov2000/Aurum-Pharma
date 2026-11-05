"""Public FEFO checkout respects immutable lots under a controlled pharmacy clock."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_physical_blocking import PhysicalLotBlockingService
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import receive_source, remaining
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_checkout_api import cash_path, quoted_request
from tests.domains.pos.test_physical_cash_competition import new_sale
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def public_unit_sale(
    client: AsyncClient, session: AsyncSession, quantity: int
) -> PhysicalCheckoutResultV3:
    sale = await new_sale(session)
    sale_id = sale.id
    item = await client.put(
        f"/api/v1/sales/{sale_id}/physical-cart/items/{uuid4()}",
        json={
            "catalog_id": str(runtime(session).stock.catalog_id),
            "position": 1,
            "sale_format": "unit",
            "quantity": quantity,
            "discount_amount": "0.00",
        },
    )
    assert item.status_code == 200
    request = await quoted_request(client, sale_id)
    body = request.model_dump(mode="json")
    path = cash_path(sale_id)
    assert (await client.post(f"{path}/intent", json=body)).status_code == 200
    response = await client.post(f"{path}/complete", json=body)
    assert response.status_code == 200
    payload = response.json()
    result = PhysicalCheckoutResultV3.model_validate(payload)
    context = runtime(session)
    await session.commit()
    await set_receiving_context(session, context.stock, context.actor_id)
    replay = await client.post(f"{path}/complete", json=body)
    assert replay.status_code == 200 and replay.json() == payload
    status = await client.get(f"{path}/operations/{request.operation_id}")
    assert status.status_code == 200 and status.json()["result"] == payload
    assert result.lines[0].base_units == quantity
    assert result.total_amount == Decimal(quantity)
    return result


async def lot_facts(session: AsyncSession, lot_id: UUID) -> dict[str, list[object]]:
    result: dict[str, list[object]] = {}
    for table, key in (
        ("unit_stock_lot_v2", "id"),
        ("unit_stock_opened_pack_v2", "lot_id"),
        ("unit_stock_movement_v2", "lot_id"),
        ("unit_stock_entry_v2", "lot_id"),
    ):
        result[table] = list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(r) FROM public.{table} r WHERE {key}=:lot "
                        "ORDER BY to_jsonb(r)::text"
                    ),
                    {"lot": lot_id},
                )
            ).scalars()
        )
    return result


async def assert_selected_lots(
    session: AsyncSession,
    result: PhysicalCheckoutResultV3,
    earlier_id: UUID,
    later_id: UUID,
    quantity: int,
) -> None:
    movements = (
        await session.execute(
            text(
                "SELECT lot_id,sum(units_taken),sum(cost_amount) FROM unit_stock_movement_v2 "
                "WHERE command_id=:command AND units_taken>0 GROUP BY lot_id"
            ),
            {"command": result.operation_id},
        )
    ).all()
    expected = {earlier_id: (4, Decimal("1.33"))}
    if quantity == 61:
        expected = {earlier_id: (60, Decimal("20.00")), later_id: (1, Decimal("0.33"))}
    assert {row.lot_id: (row[1], row[2]) for row in movements} == expected
    payments = (
        await session.execute(
            text("SELECT payment_method,amount FROM sale_payment WHERE sale_id=:sale"),
            {"sale": result.sale_id},
        )
    ).all()
    assert len(payments) == 1 and tuple(payments[0]) == ("cash", Decimal(quantity))
    assert (
        await session.scalar(
            text("SELECT count(*) FROM pos_physical_checkout WHERE sale_id=:sale"),
            {"sale": result.sale_id},
        )
        == 1
    )
    receipt = await session.scalar(
        text("SELECT receipt_snapshot FROM sale WHERE id=:sale"), {"sale": result.sale_id}
    )
    assert receipt["schema_version"] == 3
    assert receipt["receipt_number"] == result.receipt_number
    assert receipt["total_amount"] == str(result.total_amount)


@pytest.mark.parametrize("quantity", [4, 61])
async def test_public_cash_fefo_skips_expired_and_blocked_before_later_opened_stock(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, quantity: int
) -> None:
    context = runtime(db_session)
    later = await receive_source(db_session, expires_in=180)
    async with cart_actor(db_session):
        opening_sale = await public_unit_sale(client, db_session, 3)
        assert await remaining(db_session, later) == 57
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM unit_stock_opened_pack_v2 WHERE lot_id=:lot"),
                {"lot": later.lot_id},
            )
            == 1
        )
        expired = await receive_source(db_session, expires_in=5)
        blocked = await receive_source(db_session, expires_in=20)
        earlier = await receive_source(db_session, expires_in=30)
        await PhysicalLotBlockingService(UnitDispensingV2Repository(db_session)).set_blocked(
            tenant_id=context.stock.tenant_id,
            branch_id=context.stock.branch_id,
            lot_id=blocked.lot_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            is_blocked=True,
        )
        before = {
            source.lot_id: await lot_facts(db_session, source.lot_id)
            for source in (later, expired, blocked)
        }
        # All lots were received with future expiry. Advance both application stock
        # clocks without rewriting immutable packaging, expiry or any database guard.
        clock = datetime.combine(date.today() + timedelta(days=10), time(12), tzinfo=UTC)
        monkeypatch.setattr("app.domains.pos.unit_checkout_preparation.utc_now", lambda: clock)
        monkeypatch.setattr(
            "app.domains.inventory.unit_dispensing_v2_service.utc_now", lambda: clock
        )
        selected = await public_unit_sale(client, db_session, quantity)
        assert selected.sale_id != opening_sale.sale_id
        assert (selected.register_id, selected.receipt_number) != (
            opening_sale.register_id,
            opening_sale.receipt_number,
        )
        await assert_selected_lots(db_session, selected, earlier.lot_id, later.lot_id, quantity)
        assert await remaining(db_session, earlier) == max(0, 60 - quantity)
        for source in (expired, blocked):
            assert await lot_facts(db_session, source.lot_id) == before[source.lot_id]
        if quantity == 4:
            assert await lot_facts(db_session, later.lot_id) == before[later.lot_id]
        assert await remaining(db_session, later) == 57 - max(0, quantity - 60)
        assert await remaining(db_session, expired) == await remaining(db_session, blocked) == 60
