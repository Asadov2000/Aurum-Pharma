"""Public checkout respects distinct opened-pack identities and dispense steps."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import packs, receive_source, remaining
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_checkout import facts
from tests.domains.pos.test_physical_cash_checkout_api import cash_path, quoted_request
from tests.domains.pos.test_physical_cash_competition import new_sale
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def prepare_cash_cart(
    client: AsyncClient,
    session: AsyncSession,
    lines: tuple[tuple[SaleFormatV2, int], ...],
) -> CashConfirmationRequestV2:
    sale = await new_sale(session)
    for position, (sale_format, quantity) in enumerate(lines, 1):
        response = await client.put(
            f"/api/v1/sales/{sale.id}/physical-cart/items/{uuid4()}",
            json={
                "catalog_id": str(runtime(session).stock.catalog_id),
                "position": position,
                "sale_format": sale_format,
                "quantity": quantity,
                "discount_amount": "0.00",
            },
        )
        assert response.status_code == 200
    request = await quoted_request(client, sale.id)
    response = await client.post(
        f"{cash_path(sale.id)}/intent", json=request.model_dump(mode="json")
    )
    assert response.status_code == 200
    return request


async def complete_cash_cart(
    client: AsyncClient, session: AsyncSession, request: CashConfirmationRequestV2
) -> PhysicalCheckoutResultV3:
    path = cash_path(request.sale_id)
    body = request.model_dump(mode="json")
    response = await client.post(f"{path}/complete", json=body)
    assert response.status_code == 200
    payload = response.json()
    result = PhysicalCheckoutResultV3.model_validate(payload)
    context = runtime(session)
    await session.commit()
    await set_receiving_context(session, context.stock, context.actor_id)
    before = await facts(session)
    replay = await client.post(f"{path}/complete", json=body)
    assert replay.status_code == 200 and replay.json() == payload
    status = await client.get(f"{path}/operations/{request.operation_id}")
    assert status.status_code == 200 and status.json()["result"] == payload
    assert await facts(session) == before
    return result


async def assert_open_pack_allocations(
    session: AsyncSession,
    result: PhysicalCheckoutResultV3,
    first_pack: UUID,
    second_pack: UUID,
) -> None:
    allocations = (
        await session.execute(
            text(
                "SELECT pack_id,sum(units_taken),sum(cost_amount) FROM unit_stock_movement_v2 "
                "WHERE command_id=:command AND units_taken>0 GROUP BY pack_id"
            ),
            {"command": result.operation_id},
        )
    ).all()
    assert {row.pack_id: (row[1], row[2]) for row in allocations} == {
        first_pack: (5, Decimal("1.67")),
        second_pack: (13, Decimal("4.33")),
    }
    blister = next(line for line in result.lines if line.sale_format == "blister")
    blister_packs = (
        await session.execute(
            text(
                "SELECT pack_id,kind,units_taken FROM unit_stock_movement_v2 "
                "WHERE command_id=:command AND line_id=:line AND units_taken>0"
            ),
            {"command": result.operation_id, "line": blister.id},
        )
    ).all()
    assert [tuple(row) for row in blister_packs] == [(second_pack, "blister_take", 10)]
    payments = (
        await session.execute(
            text("SELECT payment_method,amount FROM sale_payment WHERE sale_id=:sale"),
            {"sale": result.sale_id},
        )
    ).all()
    assert [tuple(row) for row in payments] == [("cash", Decimal("18.00"))]
    assert (
        await session.scalar(
            text("SELECT count(*) FROM pos_physical_checkout WHERE sale_id=:sale"),
            {"sale": result.sale_id},
        )
        == 1
    )


@pytest.mark.parametrize("unit_first", [False, True])
async def test_public_cash_uses_real_blister_and_units_across_two_open_packs(
    client: AsyncClient, db_session: AsyncSession, unit_first: bool
) -> None:
    source = await receive_source(db_session)
    async with cart_actor(db_session):
        first = await complete_cash_cart(
            client, db_session, await prepare_cash_cart(client, db_session, (("unit", 25),))
        )
        second = await complete_cash_cart(
            client, db_session, await prepare_cash_cart(client, db_session, (("blister", 1),))
        )
        before = await packs(db_session, source)
        assert [pack[2] for pack in before] == [5, 20]
        assert await remaining(db_session, source) == 25
        lines: tuple[tuple[SaleFormatV2, int], ...] = (
            (("unit", 8), ("blister", 1)) if unit_first else (("blister", 1), ("unit", 8))
        )
        result = await complete_cash_cart(
            client, db_session, await prepare_cash_cart(client, db_session, lines)
        )
        assert tuple(line.sale_format for line in result.lines) == tuple(line[0] for line in lines)
        assert result.total_amount == Decimal("18.00")
        receipt_keys = {(sale.register_id, sale.receipt_number) for sale in (first, second, result)}
        assert len(receipt_keys) == 3
        await assert_open_pack_allocations(db_session, result, before[0][0], before[1][0])
        after = await packs(db_session, source)
        assert [pack[0] for pack in after] == [pack[0] for pack in before]
        assert [pack[2] for pack in after] == [0, 7]
        assert await remaining(db_session, source) == 7


async def assert_unposted_cash_cart(
    session: AsyncSession, request: CashConfirmationRequestV2
) -> None:
    sale = (
        await session.execute(
            text("SELECT status,receipt_number,completed_at FROM sale WHERE id=:sale"),
            {"sale": request.sale_id},
        )
    ).one()
    assert tuple(sale) == ("draft", None, None)
    for table in ("sale_payment", "pos_physical_checkout"):
        assert (
            await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE sale_id=:sale"),
                {"sale": request.sale_id},
            )
            == 0
        )


async def test_public_cash_cannot_join_small_open_pack_remainders_to_bypass_step(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await receive_source(db_session, step=6)
    sequence: tuple[tuple[SaleFormatV2, int], ...] = (("unit", 6), ("blister", 3), ("unit", 18))
    async with cart_actor(db_session):
        for sale_format, quantity in sequence:
            await complete_cash_cart(
                client,
                db_session,
                await prepare_cash_cart(client, db_session, ((sale_format, quantity),)),
            )
        opened = await packs(db_session, source)
        assert sorted(pack[2] for pack in opened) == [2, 4]
        assert await remaining(db_session, source) == 6
        request = await prepare_cash_cart(client, db_session, (("unit", 6),))
        context = runtime(db_session)
        await db_session.commit()
        await set_receiving_context(db_session, context.stock, context.actor_id)
        before = await facts(db_session)
        response = await client.post(
            f"{cash_path(request.sale_id)}/complete", json=request.model_dump(mode="json")
        )
        assert response.status_code == 422
        assert "Insufficient compatible physical stock" in response.text
        assert await facts(db_session) == before
        await assert_unposted_cash_cart(db_session, request)
        assert await packs(db_session, source) == opened
        assert await remaining(db_session, source) == 6
