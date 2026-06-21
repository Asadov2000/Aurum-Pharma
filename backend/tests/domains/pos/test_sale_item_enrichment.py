"""SaleItemRead enrichment: each line carries its FEFO batch's number, expiry
and days-to-expiry (additive, read-only). FEFO selection and pricing are
unchanged — the unit price is still the batch sale price."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _open_shift(service: POSService, s) -> None:  # type: ignore[no-untyped-def]
    try:
        await service.open_shift(
            tenant_id=s["tenant"].id,
            register_id=s["register"].id,
            opened_by_user_id=s["cashier"].id,
            opening_cash=Decimal("0"),
        )
    except Exception:
        pass  # already open


async def test_get_sale_details_enriches_batch_and_expiry(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(batch_qty=100, sale_price=10)
    service = POSService(POSRepository(db_session))
    await _open_shift(service, s)

    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal(2))

    _sale, rows, _payments = await service.get_sale_details(sale.id)
    assert len(rows) == 1
    item, batch_number, expires_at, days_to_expiry = rows[0]

    # Enrichment points at the FEFO-chosen batch.
    assert item.batch_id == s["batch"].id
    assert batch_number == s["batch"].batch_number
    assert expires_at == s["batch"].expires_at
    assert days_to_expiry is not None and 170 <= days_to_expiry <= 181

    # FEFO/pricing untouched: qty and the batch sale price are unchanged.
    assert item.qty == Decimal("2")
    assert item.unit_price == Decimal("10")
