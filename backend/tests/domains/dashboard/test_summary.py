"""Dashboard summary aggregation — exercises all four sections."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.dashboard.repository import DashboardRepository
from app.domains.dashboard.service import DashboardService
from app.domains.foundation.models import Branch
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository


async def _seed_tenant_with_branch(db: AsyncSession):  # type: ignore[no-untyped-def]
    nick = uuid4().hex[:8]
    foundation = FoundationService(FoundationRepository(db))
    tenant = await foundation.create_tenant(
        payload={"name": f"Dash {nick}", "contact_email": f"d-{nick}@aurum.tj"}
    )
    branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
    return tenant, branch


async def test_summary_empty_tenant_zeroed(db_session: AsyncSession) -> None:
    tenant, _ = await _seed_tenant_with_branch(db_session)
    service = DashboardService(DashboardRepository(db_session), redis=None)

    summary = await service.get_summary(tenant.id)

    assert summary.today.revenue == Decimal("0")
    assert summary.today.receipts == 0
    assert summary.today.active_shifts == 0
    assert summary.today.cashiers_on_shift == 0
    assert summary.expiring.batches == []
    assert summary.expiring.licenses == []
    assert summary.finance.open_invoices_count == 0
    assert summary.finance.has_overdue is False
    assert summary.checklist.draft_incoming_count == 0
    assert summary.checklist.latest_closed_shift_id is None


async def test_summary_surfaces_expiring_batch_and_license(db_session: AsyncSession) -> None:
    tenant, branch = await _seed_tenant_with_branch(db_session)
    catalog = CatalogService(CatalogRepository(db_session))
    inv = InventoryRepository(db_session)

    item = await catalog.create_item(tenant_id=tenant.id, fields={"brand_name": "Aspirin"})
    # A batch expiring in 20 days → "red" zone, with stock on hand.
    batch = await inv.create_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        expires_at=date.today() + timedelta(days=20),
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("10.00"),
        qty_initial=Decimal("10"),
        qty_remaining=Decimal("0"),
    )
    await inv.insert_movement(
        tenant_id=tenant.id,
        batch_id=batch.id,
        movement_type="incoming",
        qty_delta=Decimal("10"),
        source_table=None,
        source_id=None,
    )
    # License expiring in 15 days → inside the 30-day window.
    await db_session.execute(
        Branch.__table__.update()
        .where(Branch.id == branch.id)
        .values(license_expires_at=date.today() + timedelta(days=15))
    )

    service = DashboardService(DashboardRepository(db_session), redis=None)
    summary = await service.get_summary(tenant.id)

    assert len(summary.expiring.batches) == 1
    b = summary.expiring.batches[0]
    assert b.expiry_status == "red"
    assert b.qty_remaining == Decimal("10")
    assert b.days_to_expiry == 20

    assert len(summary.expiring.licenses) == 1
    assert summary.expiring.licenses[0].branch_name == "Main"
    assert summary.expiring.licenses[0].days_left == 15
