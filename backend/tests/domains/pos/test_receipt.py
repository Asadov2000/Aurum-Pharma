"""Receipt assembly (build_receipt) + PDF rendering."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.receipt_pdf import render_receipt_pdf
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _completed_sale(db_session: AsyncSession, pos_scaffold):  # type: ignore[no-untyped-def]
    s = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("2"))
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("20.00"))
    completed = await service.complete(sale_id=sale.id)
    return service, s, completed


async def test_build_receipt_resolves_names_and_totals(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, s, sale = await _completed_sale(db_session, pos_scaffold)

    receipt = await service.build_receipt(sale.id)

    # Header resolved from tenant/branch — names, not UUIDs.
    assert receipt.pharmacy_name == s["tenant"].name
    assert receipt.branch_name == "Main"
    assert receipt.cashier_name == "Cashier"
    assert receipt.is_refund is False
    assert receipt.status == "completed"
    assert receipt.receipt_number is not None

    # One line, fully resolved.
    assert len(receipt.items) == 1
    line = receipt.items[0]
    assert line.name == s["item"].brand_name
    assert line.qty == Decimal("2")
    assert line.unit_price == Decimal("10.00")
    assert line.total_price == Decimal("20.00")

    # Totals + payment + change.
    assert receipt.total == Decimal("20.00")
    assert receipt.discount_total == Decimal("0")
    assert len(receipt.payments) == 1
    assert receipt.payments[0].method == "cash"
    assert receipt.payments[0].amount == Decimal("20.00")
    assert receipt.paid_total == Decimal("20.00")
    assert receipt.change == Decimal("0")


async def test_render_receipt_pdf_produces_pdf_bytes(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, _s, sale = await _completed_sale(db_session, pos_scaffold)
    receipt = await service.build_receipt(sale.id)

    pdf = render_receipt_pdf(receipt)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 500


async def test_build_receipt_for_draft_has_no_receipt_number(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=Decimal("1"))

    receipt = await service.build_receipt(sale.id)
    assert receipt.status == "draft"
    assert receipt.receipt_number is None
    assert receipt.total == Decimal("10.00")
    assert receipt.change == Decimal("0")  # nothing paid yet
