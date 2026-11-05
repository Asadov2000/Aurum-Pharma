"""Ordinary quarantine remains visible without inventing historical purchase costs."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.quarantine_report_repository import QuarantineReportRepository
from tests.domains.pos.test_refund import _open_shift_and_sell
from tests.domains.pos.test_stock_on_date import _stock_reader


async def test_legacy_quarantine_unknown_value_does_not_use_current_purchase_price(
    db_session: AsyncSession, pos_scaffold: object
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(db_session, pos_scaffold, qty=2)
    await service.refund(
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
    )
    await _stock_reader(db_session, scaffold["tenant"].id)
    cutoff = await db_session.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(cutoff, datetime)
    report = await QuarantineReportRepository(db_session).snapshot(
        tenant_id=scaffold["tenant"].id,
        before=cutoff + timedelta(seconds=1),
        branch_id=scaffold["branch"].id,
        limit=None,
        offset=0,
    )
    assert report.total == 1 and report.total_qty == Decimal("1")
    assert report.total_value is None and report.known_total_value == Decimal("0")
    row = report.rows[0]
    assert row.source_kind == row.inventory_kind == "legacy"
    assert row.name == scaffold["item"].brand_name
    assert row.cost_status == "unknown" and row.value is None
    assert row.physical_quantity is None and report.physical_quantities == []
