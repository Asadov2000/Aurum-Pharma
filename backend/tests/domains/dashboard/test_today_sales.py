"""Dashboard 'today' revenue tile: tenant-timezone day boundary, is_test
exclusion, gross (same-day refund stays in, matching the sales-summary), and
the reports.view authorization gate."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.dashboard.repository import DashboardRepository
from app.domains.dashboard.service import DashboardService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


async def _seed(db: AsyncSession):  # type: ignore[no-untyped-def]
    """Active tenant + branch + register + cashier + stocked batch + open shift."""
    nick = uuid4().hex[:8]
    foundation = FoundationService(FoundationRepository(db))
    catalog = CatalogService(CatalogRepository(db))
    inv = InventoryRepository(db)

    tenant = await foundation.create_tenant(
        payload={"name": f"Dash {nick}", "contact_email": f"d-{nick}@aurum.tj"}
    )
    await foundation.update_tenant(tenant.id, fields={"status": "active"})
    await db.refresh(tenant)
    branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
    register = await foundation.create_register(
        tenant_id=tenant.id, fields={"branch_id": branch.id, "name": "Касса 1"}
    )
    item = await catalog.create_item(tenant_id=tenant.id, fields={"brand_name": f"Drug {nick}"})
    batch = await inv.create_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        expires_at=date.today() + timedelta(days=180),
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("10.00"),
        qty_initial=Decimal("1000"),
        qty_remaining=Decimal("0"),
    )
    await inv.insert_movement(
        tenant_id=tenant.id,
        batch_id=batch.id,
        movement_type="incoming",
        qty_delta=Decimal("1000"),
        source_table=None,
        source_id=None,
    )
    cashier = AppUser(
        email=f"c-{nick}@aurum.tj", full_name="Cashier", home_tenant_id=tenant.id, status="active"
    )
    db.add(cashier)
    await db.flush()
    await db.refresh(cashier)

    pos = POSService(POSRepository(db))
    await pos.open_shift(
        tenant_id=tenant.id,
        register_id=register.id,
        opened_by_user_id=cashier.id,
        opening_cash=Decimal("0"),
    )
    return tenant, register, cashier, item, pos


async def _sell(pos, tenant, register, cashier, item, *, qty, paid):  # type: ignore[no-untyped-def]
    sale = await pos.create_sale(
        tenant_id=tenant.id, register_id=register.id, cashier_user_id=cashier.id
    )
    created, _ = await pos.add_item(sale_id=sale.id, catalog_id=item.id, qty=qty)
    await pos.add_payment(sale_id=sale.id, payment_method="cash", amount=paid)
    await pos.complete(sale_id=sale.id)
    return sale, created


def _dash(db: AsyncSession) -> DashboardService:
    return DashboardService(DashboardRepository(db), redis=None)


async def test_today_sales_uses_tenant_timezone(db_session: AsyncSession) -> None:
    """A sale stamped 00:30 Dushanbe (= the previous UTC day) belongs to the
    local 'today'; one stamped yesterday-local does not."""
    tenant, register, cashier, item, pos = await _seed(db_session)
    a, _ = await _sell(pos, tenant, register, cashier, item, qty=Decimal("1"), paid=Decimal("10"))
    b, _ = await _sell(pos, tenant, register, cashier, item, qty=Decimal("1"), paid=Decimal("10"))

    # A → today 00:30 Dushanbe (its UTC instant is the previous day ~19:30Z).
    await db_session.execute(
        text(
            "UPDATE sale SET completed_at = "
            "(date_trunc('day', now() AT TIME ZONE 'Asia/Dushanbe') + interval '30 minutes') "
            "AT TIME ZONE 'Asia/Dushanbe' WHERE id = :id"
        ),
        {"id": str(a.id)},
    )
    # B → yesterday noon Dushanbe (clearly the previous local day).
    await db_session.execute(
        text(
            "UPDATE sale SET completed_at = "
            "(date_trunc('day', now() AT TIME ZONE 'Asia/Dushanbe') - interval '12 hours') "
            "AT TIME ZONE 'Asia/Dushanbe' WHERE id = :id"
        ),
        {"id": str(b.id)},
    )

    summary = await _dash(db_session).get_summary(tenant.id)
    assert summary.today.revenue == Decimal("10.00")  # only A (today local)
    assert summary.today.receipts == 1


async def test_today_sales_excludes_test_sales(db_session: AsyncSession) -> None:
    tenant, register, cashier, item, pos = await _seed(db_session)
    await _sell(pos, tenant, register, cashier, item, qty=Decimal("1"), paid=Decimal("10"))
    test_sale, _ = await _sell(
        pos, tenant, register, cashier, item, qty=Decimal("1"), paid=Decimal("10")
    )
    await db_session.execute(
        text("UPDATE sale SET is_test = true WHERE id = :id"), {"id": str(test_sale.id)}
    )

    summary = await _dash(db_session).get_summary(tenant.id)
    assert summary.today.revenue == Decimal("10.00")  # test sale excluded
    assert summary.today.receipts == 1


async def test_today_sales_gross_keeps_same_day_full_refund_and_matches_summary(
    db_session: AsyncSession,
) -> None:
    """A sale completed and fully refunded today stays in the gross tile, and the
    tile equals the gross row of today's sales-summary."""
    tenant, register, cashier, item, pos = await _seed(db_session)
    sale, created = await _sell(
        pos, tenant, register, cashier, item, qty=Decimal("1"), paid=Decimal("10")
    )
    await pos.refund(
        parent_sale_id=sale.id,
        items=[(created[0].id, Decimal("1"))],
        reason="defect",
        comment=None,
        cashier_user_id=cashier.id,
    )

    summary = await _dash(db_session).get_summary(tenant.id)
    assert summary.today.revenue == Decimal("10.00")  # gross — refund doesn't remove it

    # Same number as the sales-summary gross row for the local "today".
    local_today = (
        await db_session.execute(text("SELECT (now() AT TIME ZONE 'Asia/Dushanbe')::date"))
    ).scalar_one()
    report = await pos.build_sales_summary(
        tenant_id=tenant.id, date_from=local_today, date_to=local_today, branch_id=None
    )
    assert report.gross_sales == summary.today.revenue == Decimal("10.00")


async def test_dashboard_summary_requires_reports_view(
    db_session: AsyncSession, auth_client: AsyncClient
) -> None:
    """Seller (no reports.view) → 403; administrator → 200."""
    foundation = FoundationService(FoundationRepository(db_session))
    nick = uuid4().hex[:8]
    tenant = await foundation.create_tenant(
        payload={"name": f"Dash {nick}", "contact_email": f"d-{nick}@aurum.tj"}
    )
    await foundation.update_tenant(tenant.id, fields={"status": "active"})

    seller = AppUser(
        email=f"seller-{uuid4().hex[:8]}@aurum.tj",
        full_name="Seller",
        home_tenant_id=tenant.id,
        status="active",
    )
    admin = AppUser(
        email=f"admin-{uuid4().hex[:8]}@aurum.tj",
        full_name="Admin",
        home_tenant_id=tenant.id,
        is_administrator=True,
        status="active",
    )
    db_session.add_all([seller, admin])
    await db_session.flush()
    await db_session.refresh(seller)
    await db_session.refresh(admin)

    seller_token = create_access_token(
        seller.id, tenant_id=tenant.id, is_developer=False, is_administrator=False
    )
    admin_token = create_access_token(
        admin.id, tenant_id=tenant.id, is_developer=False, is_administrator=True
    )
    url = "/api/v1/dashboard/summary"

    seller_resp = await auth_client.get(url, headers={"Authorization": f"Bearer {seller_token}"})
    assert seller_resp.status_code == 403

    admin_resp = await auth_client.get(url, headers={"Authorization": f"Bearer {admin_token}"})
    assert admin_resp.status_code == 200
