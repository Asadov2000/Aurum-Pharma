"""Z-report data assembly, the closed-only guard, and the MinIO cache."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _complete_sale(
    service: POSService,
    s,
    *,
    qty: Decimal,
    payments: list[tuple[str, Decimal]],
    is_test: bool = False,
):  # type: ignore[no-untyped-def]
    sale = await service.create_sale(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        cashier_user_id=s["cashier"].id,
    )
    created, _ = await service.add_item(sale_id=sale.id, catalog_id=s["item"].id, qty=qty)
    for method, amount in payments:
        await service.add_payment(sale_id=sale.id, payment_method=method, amount=amount)
    if is_test:
        await service.repo.update_sale(sale, is_test=True)
    await service.complete(sale_id=sale.id)
    return sale, created


async def test_build_z_report_aggregates_and_breakdown(
    db_session: AsyncSession, pos_scaffold
) -> None:
    s = await pos_scaffold(sale_price=Decimal("10"), batch_qty=100)
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("100"),
    )

    # A: 2 units (20) — all cash. B: 1 unit (10) — card. C: 1 unit (10) — mixed.
    sale_a, created_a = await _complete_sale(
        service, s, qty=Decimal("2"), payments=[("cash", Decimal("20"))]
    )
    await _complete_sale(service, s, qty=Decimal("1"), payments=[("card", Decimal("10"))])
    await _complete_sale(
        service, s, qty=Decimal("1"), payments=[("cash", Decimal("5")), ("card", Decimal("5"))]
    )

    # Partial refund of A (1 of 2 units) → parent stays completed, one return.
    await service.refund(
        parent_sale_id=sale_a.id,
        items=[(created_a[0].id, Decimal("1"))],
        reason="defect",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )

    # Close short by declaring less than expected.
    shift = await service.repo.get_open_shift_for_register(s["register"].id)
    assert shift is not None
    closed = await service.close_shift(
        shift_id=shift.id,
        closing_cash_actual=Decimal("130"),
        closed_by_user_id=s["cashier"].id,
        notes="пересчёт кассы",
    )

    z = await service.build_z_report(shift.id)

    assert z.status == "closed"
    assert z.pharmacy_name == s["tenant"].name
    assert z.register_name == "Касса 1"
    assert z.cashier_name == "Cashier"

    # Sales / discounts / returns (A counts even though 1 unit was refunded).
    assert z.sales_count == 3
    assert z.total_sales == Decimal("40.00")
    assert z.total_discounts == Decimal("0")
    assert z.returns_count == 1
    assert z.total_refunds == Decimal("10.00")

    # Payment breakdown: A→cash, B→card, C→mixed.
    assert z.payment_breakdown.cash == Decimal("20.00")
    assert z.payment_breakdown.card == Decimal("10.00")
    assert z.payment_breakdown.bank_transfer == Decimal("0")
    assert z.payment_breakdown.mixed == Decimal("10.00")

    # Cash reconciliation mirrors the closed shift.
    assert z.initial_cash == Decimal("100.00")
    assert z.actual_cash == Decimal("130.00")
    assert z.expected_cash == closed.closing_cash_expected
    assert z.cash_difference == closed.closing_difference
    assert z.difference_reason == "пересчёт кассы"

    api_report = await service.z_report(shift.id)
    assert api_report["register_id"] == s["register"].id
    assert api_report["cashier_user_id"] == s["cashier"].id
    assert api_report["totals"]["sales_total"] == Decimal("40.00")
    assert api_report["totals"]["returns_total"] == Decimal("10.00")
    assert api_report["totals"]["by_method"]["mixed"] == Decimal("10.00")


async def test_z_report_full_refund_counts_sale_and_return(
    db_session: AsyncSession, pos_scaffold
) -> None:
    """Same-shift full refund: total_sales=100 AND total_refunds=100 (the voided
    sale still counts), so gross/refunds stay consistent."""
    s = await pos_scaffold(sale_price=Decimal("100"), batch_qty=10)
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale, created = await _complete_sale(
        service, s, qty=Decimal("1"), payments=[("cash", Decimal("100"))]
    )
    await service.refund(
        parent_sale_id=sale.id,
        items=[(created[0].id, Decimal("1"))],
        reason="defect",
        comment=None,
        cashier_user_id=s["cashier"].id,
    )
    shift = await service.repo.get_open_shift_for_register(s["register"].id)
    assert shift is not None
    closed = await service.close_shift(
        shift_id=shift.id,
        closing_cash_actual=Decimal("0"),
        closed_by_user_id=s["cashier"].id,
        notes=None,
    )
    assert closed.closing_cash_expected == Decimal("0")

    z = await service.build_z_report(shift.id)
    assert z.total_sales == Decimal("100.00")
    assert z.total_refunds == Decimal("100.00")
    assert z.sales_count == 1
    assert z.returns_count == 1
    assert z.payment_breakdown.cash == Decimal("100.00")


async def test_z_report_excludes_test_sales(db_session: AsyncSession, pos_scaffold) -> None:
    """is_test sales never reach the Z-report money totals or breakdown."""
    s = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    await _complete_sale(service, s, qty=Decimal("1"), payments=[("cash", Decimal("10"))])
    await _complete_sale(
        service,
        s,
        qty=Decimal("1"),
        payments=[("cash", Decimal("10"))],
        is_test=True,
    )

    shift = await service.repo.get_open_shift_for_register(s["register"].id)
    assert shift is not None
    await service.close_shift(
        shift_id=shift.id,
        closing_cash_actual=Decimal("10"),
        closed_by_user_id=s["cashier"].id,
        notes=None,
    )

    z = await service.build_z_report(shift.id)
    assert z.total_sales == Decimal("10.00")  # only the real sale
    assert z.sales_count == 1
    assert z.payment_breakdown.cash == Decimal("10.00")


async def test_z_report_xlsx_rejects_open_shift(db_session: AsyncSession, pos_scaffold) -> None:
    s = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    await service.open_shift(
        tenant_id=s["tenant"].id,
        register_id=s["register"].id,
        opened_by_user_id=s["cashier"].id,
        opening_cash=Decimal("0"),
    )
    shift = await service.repo.get_open_shift_for_register(s["register"].id)
    assert shift is not None

    with pytest.raises(BusinessRuleError):
        await service.get_z_report_xlsx(shift.id)


async def test_z_report_xlsx_is_cached_and_identical(
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
    await _complete_sale(service, s, qty=Decimal("1"), payments=[("cash", Decimal("10"))])
    shift = await service.repo.get_open_shift_for_register(s["register"].id)
    assert shift is not None
    await service.close_shift(
        shift_id=shift.id,
        closing_cash_actual=Decimal("10"),
        closed_by_user_id=s["cashier"].id,
        notes=None,
    )

    first = await service.get_z_report_xlsx(shift.id)
    second = await service.get_z_report_xlsx(shift.id)
    assert first[:2] == b"PK"  # valid xlsx (zip) magic
    assert len(first) > 500
    assert first == second  # second served from the MinIO cache
