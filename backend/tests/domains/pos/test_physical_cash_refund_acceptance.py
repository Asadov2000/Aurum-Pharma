"""Public partial cash refunds conserve historical money across pack coefficients."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2
from app.domains.pos.physical_cash_refund_status_schemas import PhysicalRefundOperationCompleted
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.incoming.test_unit_acceptance_v2 import accept_v2, seed_v2_incoming
from tests.domains.inventory.test_unit_ledger_v2_dispensing import PhysicalSource, remaining
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_competition import new_sale
from tests.domains.pos.test_physical_cash_refund import facts
from tests.domains.pos.test_physical_cash_refund_api import items, path, request
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields, refund_actor
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def discounted_source(
    session: AsyncSession, units: int
) -> tuple[PhysicalCheckoutResultV3, PhysicalSource]:
    context = runtime(session)
    await session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=1.01,unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{units_per_pack}',CAST(:units AS jsonb)) WHERE id=:id"
        ),
        {"units": str(units), "id": context.stock.catalog_id},
    )
    incoming = await seed_v2_incoming(session, blister=None)
    await session.execute(
        text("UPDATE incoming_item SET qty=1,purchase_price=0.10,sale_price=1.01 WHERE id=:id"),
        {"id": incoming.item_ids[0]},
    )
    await accept_v2(session, incoming)
    row = (
        await session.execute(
            text("SELECT id,packaging FROM unit_stock_lot_v2 WHERE source_item_id=:id"),
            {"id": incoming.item_ids[0]},
        )
    ).one()
    stock = PhysicalSource(incoming, row.id, UnitPackagingV2.model_validate(row.packaging))
    sale = await new_sale(session)
    await put_physical(session, sale.id, sale_format="pack", discount=Decimal("0.01"))
    original = await complete(session, await saved_request(session, sale))
    settings_repository = FoundationRepository(session)
    settings = await settings_repository.get_settings(context.stock.tenant_id)
    assert settings is not None
    await settings_repository.update_settings(settings, refund_reason_mode="off")
    await session.commit()
    await set_receiving_context(session, context.stock, context.actor_id)
    assert original.total_amount == Decimal("1.00")
    assert original.lines[0].base_units == units
    assert original.lines[0].discount_amount == Decimal("0.01")
    assert await remaining(session, stock) == 0
    return original, stock


async def return_one_unit(
    client: AsyncClient, session: AsyncSession, original: PhysicalCheckoutResultV3
) -> tuple[PhysicalRefundOperationCompleted, dict[str, object]]:
    body = await request(client, original)
    response = await client.post(f"{path(original.sale_id)}/complete", json=body)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert_no_cost_fields(payload)
    result = PhysicalRefundOperationCompleted.model_validate(payload)
    context = runtime(session)
    # The HTTP test override does not commit; force every payout's deferred guards.
    await session.commit()
    await set_receiving_context(session, context.stock, context.actor_id)
    replay = await client.post(f"{path(original.sale_id)}/complete", json=body)
    assert replay.status_code == 200 and replay.json() == payload
    status = await client.get(f"{path(original.sale_id)}/operations/{result.operation_id}")
    assert status.status_code == 200 and status.json() == payload
    assert_no_cost_fields(status.json())
    return result, body


async def assert_refund_journals(
    session: AsyncSession, original: PhysicalCheckoutResultV3, units: int, lot_id: UUID
) -> None:
    amounts = (
        await session.execute(
            text(
                "SELECT count(*),sum(r.base_units),sum(r.net_amount),sum(r.discount_amount),"
                "sum(r.gross_amount),sum(r.cost_amount) FROM pos_physical_return_line r "
                "JOIN pos_physical_return_command c "
                "ON c.tenant_id=r.tenant_id AND c.id=r.command_id "
                "WHERE c.tenant_id=:tenant AND c.parent_sale_id=:sale"
            ),
            {"tenant": original.tenant_id, "sale": original.sale_id},
        )
    ).one()
    assert tuple(amounts) == (
        units,
        units,
        Decimal("1.00"),
        Decimal("0.01"),
        Decimal("1.01"),
        Decimal("0.10"),
    )
    quarantined = (
        await session.execute(
            text(
                "SELECT sum(base_units),sum(cost_amount) FROM pos_physical_return_slice "
                "WHERE tenant_id=:tenant AND lot_id=:lot"
            ),
            {"tenant": original.tenant_id, "lot": lot_id},
        )
    ).one()
    assert tuple(quarantined) == (units, Decimal("0.10"))
    payments = (
        await session.execute(
            text(
                "SELECT count(*),sum(p.amount) FROM sale_payment p JOIN sale s ON s.id=p.sale_id "
                "AND s.tenant_id=p.tenant_id WHERE s.tenant_id=:tenant AND s.parent_sale_id=:sale "
                "AND s.sale_type='return' AND s.status='completed' AND p.payment_method='cash'"
            ),
            {"tenant": original.tenant_id, "sale": original.sale_id},
        )
    ).one()
    assert tuple(payments) == (units, Decimal("1.00"))


async def assert_overrefund_denied(
    client: AsyncClient,
    session: AsyncSession,
    original: PhysicalCheckoutResultV3,
    last_body: dict[str, object],
) -> None:
    before = await facts(session)
    response = await client.post(f"{path(original.sale_id)}/quote", json={"items": items(original)})
    assert response.status_code == 422
    response = await client.post(
        f"{path(original.sale_id)}/complete",
        json={**last_body, "operation_id": str(uuid4())},
    )
    assert response.status_code in {409, 422}
    assert await facts(session) == before


@pytest.mark.parametrize("units,last_net", [(3, "0.34"), (7, "0.15"), (30, "0.04")])
async def test_public_partial_refunds_preserve_discount_and_last_cent(
    client: AsyncClient, db_session: AsyncSession, units: int, last_net: str
) -> None:
    original, stock = await discounted_source(db_session, units)
    before = await facts(db_session)
    original_snapshot = await db_session.scalar(
        text("SELECT to_jsonb(s) FROM sale s WHERE id=:sale"), {"sale": original.sale_id}
    )
    results: list[PhysicalRefundOperationCompleted] = []
    last_body: dict[str, object] = {}
    async with refund_actor(db_session):
        for offset in range(units):
            result, last_body = await return_one_unit(client, db_session, original)
            line = result.receipt.lines[0]
            assert line.offset == offset
            assert line.quantity == line.base_units == 1 and line.sale_format == "unit"
            assert line.packaging.units_per_pack == units
            net_cents = 100 * (offset + 1) // units - 100 * offset // units
            assert line.net_amount == Decimal(net_cents) / 100
            assert line.discount_amount == (Decimal("0.01") if offset == units - 1 else Decimal(0))
            assert line.gross_amount == line.net_amount + line.discount_amount
            assert await remaining(db_session, stock) == 0
            results.append(result)
        assert results[-1].total_amount == Decimal(last_net)
        assert results[-1].receipt.lines[0].discount_amount == Decimal("0.01")
        assert sum((result.total_amount for result in results), Decimal(0)) == Decimal("1.00")
        assert len({result.operation_id for result in results}) == units
        assert len({result.sale_id for result in results}) == units
        assert len({result.receipt.receipt_number for result in results}) == units
        await assert_overrefund_denied(client, db_session, original, last_body)
    await assert_refund_journals(db_session, original, units, stock.lot_id)
    after = await facts(db_session)
    for table in before:
        if table.startswith("unit_stock_"):
            assert after[table] == before[table]
    assert (
        await db_session.scalar(
            text("SELECT to_jsonb(s) FROM sale s WHERE id=:sale"), {"sale": original.sale_id}
        )
        == original_snapshot
    )
