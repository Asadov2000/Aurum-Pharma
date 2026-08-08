"""Receipt assembly (build_receipt) + PDF rendering."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit.models import AuditLog
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
    assert receipt.datetime is not None
    assert receipt.datetime.utcoffset() == timedelta(hours=5)

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


async def test_cash_tender_is_separate_from_allocated_amount(
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
    await service.add_item(
        sale_id=sale.id,
        catalog_id=s["item"].id,
        qty=Decimal("2"),
    )
    await service.add_payment(
        sale_id=sale.id,
        payment_method="cash",
        amount=Decimal("20.00"),
        metadata={"cash_received": "50.00"},
    )
    await service.complete(sale_id=sale.id)

    receipt = await service.build_receipt(sale.id)

    assert receipt.payments[0].amount == Decimal("20.00")
    assert receipt.paid_total == Decimal("50.00")
    assert receipt.change == Decimal("30.00")


async def test_completed_receipt_keeps_original_names_after_reference_data_changes(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, scaffold, sale = await _completed_sale(db_session, pos_scaffold)
    original = await service.build_receipt(sale.id)

    scaffold["tenant"].name = "Renamed tenant"
    scaffold["branch"].name = "Renamed branch"
    scaffold["cashier"].full_name = "Renamed cashier"
    scaffold["item"].brand_name = "Renamed medicine"
    await db_session.flush()

    repeated = await service.build_receipt(sale.id)

    assert sale.receipt_snapshot is not None
    assert repeated == original


async def test_return_receipt_keeps_the_product_name_from_original_receipt(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, scaffold, parent = await _completed_sale(db_session, pos_scaffold)
    original = await service.build_receipt(parent.id)
    parent_items = await service.repo.list_items(parent.id)

    scaffold["item"].brand_name = "Renamed after sale"
    await db_session.flush()
    returned = await service.refund(
        parent_sale_id=parent.id,
        items=[(parent_items[0].id, Decimal("1"))],
        reason=None,
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
    )
    return_receipt = await service.build_receipt(returned.id)

    assert return_receipt.items[0].name == original.items[0].name


async def test_completed_receipt_snapshot_is_redacted_from_audit(
    db_session: AsyncSession, pos_scaffold
) -> None:
    _service, _scaffold, sale = await _completed_sale(db_session, pos_scaffold)

    entries = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.table_name == "sale",
                AuditLog.record_id == sale.id,
                AuditLog.action == "UPDATE",
            )
        )
    ).scalars()
    completion = next(
        entry
        for entry in entries
        if entry.new_values is not None and entry.new_values.get("status") == "completed"
    )

    assert completion.new_values is not None
    assert completion.new_values["receipt_snapshot"] == "***"


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
