"""Dashboard 'today' revenue tile: tenant-timezone day boundary, is_test
exclusion, gross (same-day refund stays in, matching the sales-summary), and
the reports.view authorization gate."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.domains.roles.models import (
    TenantMembership,
    UserAssignment,
)
from tests.auth_helpers import create_tenant_access_token
from tests.role_version_helpers import create_published_test_role


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


async def _sell(  # type: ignore[no-untyped-def]
    pos,
    tenant,
    register,
    cashier,
    item,
    *,
    qty,
    paid,
    completed_at: datetime | None = None,
    is_test: bool = False,
):
    sale = await pos.create_sale(
        tenant_id=tenant.id, register_id=register.id, cashier_user_id=cashier.id
    )
    created, _ = await pos.add_item(sale_id=sale.id, catalog_id=item.id, qty=qty)
    await pos.add_payment(sale_id=sale.id, payment_method="cash", amount=paid)
    if is_test:
        await pos.repo.update_sale(sale, is_test=True)
    completion_service = (
        pos if completed_at is None else POSService(pos.repo, now=lambda value=completed_at: value)
    )
    await completion_service.complete(sale_id=sale.id)
    return sale, created


def _dash(db: AsyncSession) -> DashboardService:
    return DashboardService(DashboardRepository(db), redis=None)


async def test_today_sales_uses_tenant_timezone(db_session: AsyncSession) -> None:
    """A sale stamped 00:30 Dushanbe (= the previous UTC day) belongs to the
    local 'today'; one stamped yesterday-local does not."""
    tenant, register, cashier, item, pos = await _seed(db_session)
    timezone = ZoneInfo("Asia/Dushanbe")
    today_local = datetime.now(timezone).date()
    today_0030 = datetime.combine(today_local, time(0, 30), timezone).astimezone(UTC)
    yesterday_noon = datetime.combine(
        today_local - timedelta(days=1),
        time(12),
        timezone,
    ).astimezone(UTC)
    await _sell(
        pos,
        tenant,
        register,
        cashier,
        item,
        qty=Decimal("1"),
        paid=Decimal("10"),
        completed_at=today_0030,
    )
    await _sell(
        pos,
        tenant,
        register,
        cashier,
        item,
        qty=Decimal("1"),
        paid=Decimal("10"),
        completed_at=yesterday_noon,
    )

    summary = await _dash(db_session).get_summary(tenant.id)
    assert summary.today.revenue == Decimal("10.00")  # only A (today local)
    assert summary.today.receipts == 1


async def test_today_sales_excludes_test_sales(db_session: AsyncSession) -> None:
    tenant, register, cashier, item, pos = await _seed(db_session)
    await _sell(pos, tenant, register, cashier, item, qty=Decimal("1"), paid=Decimal("10"))
    await _sell(
        pos,
        tenant,
        register,
        cashier,
        item,
        qty=Decimal("1"),
        paid=Decimal("10"),
        is_test=True,
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
        reason="quality_issue",
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
    """Seller without reports.view is denied; explicitly authorized admin succeeds."""
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
        status="active",
    )
    db_session.add_all([seller, admin])
    await db_session.flush()
    await db_session.refresh(seller)
    await db_session.refresh(admin)

    seller_membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=seller.id,
        full_name=seller.full_name,
        status="active",
    )
    admin_membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=admin.id,
        full_name=admin.full_name,
        status="active",
    )
    role = await create_published_test_role(
        db_session,
        tenant_id=tenant.id,
        name=f"dashboard-reporter-{uuid4().hex[:8]}",
        permission_codes=["reports.view"],
        level=2,
    )
    db_session.add_all([seller_membership, admin_membership])
    await db_session.flush()
    await db_session.refresh(admin_membership)
    db_session.add(
        UserAssignment(
            user_id=admin.id,
            tenant_id=tenant.id,
            membership_id=admin_membership.id,
            role_id=role.id,
        )
    )
    await db_session.flush()

    seller_token = await create_tenant_access_token(db_session, seller, tenant_id=tenant.id)
    admin_token = await create_tenant_access_token(db_session, admin, tenant_id=tenant.id)
    url = "/api/v1/dashboard/summary"

    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    await db_session.execute(text("SELECT set_config('app.support_access_session_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.auth_session_id', '', true)"))
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(seller.id)},
    )
    seller_resp = await auth_client.get(url, headers={"Authorization": f"Bearer {seller_token}"})
    assert seller_resp.status_code == 403

    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(admin.id)},
    )
    admin_resp = await auth_client.get(url, headers={"Authorization": f"Bearer {admin_token}"})
    assert admin_resp.status_code == 200
