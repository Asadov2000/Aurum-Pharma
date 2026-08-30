"""Dashboard summary aggregation — exercises all four sections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.dashboard.service as dashboard_service_module
from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.dashboard.repository import DashboardRepository
from app.domains.dashboard.service import DashboardService
from app.domains.foundation.models import Branch
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.models import Shift


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


async def test_summary_surfaces_expiring_batch_and_license(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(dashboard_service_module, "utc_now", lambda: fixed_now)
    today = fixed_now.date()
    tenant, branch = await _seed_tenant_with_branch(db_session)
    catalog = CatalogService(CatalogRepository(db_session))
    inv = InventoryRepository(db_session)

    item = await catalog.create_item(tenant_id=tenant.id, fields={"brand_name": "Aspirin"})
    # A batch expiring in 20 days → "red" zone, with stock on hand.
    batch = await inv.create_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        expires_at=today + timedelta(days=20),
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
        .values(license_expires_at=today + timedelta(days=15))
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


async def test_force_refresh_bypasses_cached_summary(db_session: AsyncSession) -> None:
    tenant, _ = await _seed_tenant_with_branch(db_session)
    uncached = await DashboardService(DashboardRepository(db_session), redis=None).get_summary(
        tenant.id
    )
    cached = uncached.model_copy(
        update={"today": uncached.today.model_copy(update={"revenue": Decimal("999.00")})}
    )
    redis = AsyncMock()
    redis.get.return_value = cached.model_dump_json()
    service = DashboardService(DashboardRepository(db_session), redis=redis)

    summary = await service.get_summary(tenant.id, force_refresh=True)

    assert summary.today.revenue == Decimal("0")
    redis.get.assert_not_awaited()
    redis.set.assert_awaited_once()


async def test_summary_falls_back_to_database_when_cache_is_unavailable(
    db_session: AsyncSession,
) -> None:
    tenant, _ = await _seed_tenant_with_branch(db_session)
    redis = AsyncMock()
    redis.get.side_effect = RedisError("cache unavailable")
    redis.set.side_effect = RedisError("cache unavailable")

    summary = await DashboardService(DashboardRepository(db_session), redis=redis).get_summary(
        tenant.id
    )

    assert summary.today.revenue == Decimal("0")
    redis.get.assert_awaited_once()
    redis.set.assert_awaited_once()


async def test_summary_counts_only_shifts_closed_today(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(dashboard_service_module, "utc_now", lambda: fixed_now)
    tenant, branch = await _seed_tenant_with_branch(db_session)
    foundation = FoundationService(FoundationRepository(db_session))
    register = await foundation.create_register(
        tenant_id=tenant.id,
        fields={"branch_id": branch.id, "name": "Касса 1"},
    )
    user = AppUser(
        email=f"dash-shift-{uuid4().hex[:8]}@aurum.tj",
        full_name="Owner",
        home_tenant_id=tenant.id,
        status="active",
    )
    db_session.add(user)
    await db_session.flush()
    today_shift = Shift(
        tenant_id=tenant.id,
        branch_id=branch.id,
        register_id=register.id,
        opened_by_user_id=user.id,
        closed_by_user_id=user.id,
        opened_at=fixed_now - timedelta(hours=2),
        closed_at=fixed_now - timedelta(hours=1),
        status="closed",
        opening_cash=Decimal("0"),
    )
    old_shift = Shift(
        tenant_id=tenant.id,
        branch_id=branch.id,
        register_id=register.id,
        opened_by_user_id=user.id,
        closed_by_user_id=user.id,
        opened_at=fixed_now - timedelta(days=2, hours=2),
        closed_at=fixed_now - timedelta(days=2, hours=1),
        status="closed",
        opening_cash=Decimal("0"),
    )
    db_session.add_all([today_shift, old_shift])
    await db_session.flush()

    summary = await DashboardService(DashboardRepository(db_session), redis=None).get_summary(
        tenant.id
    )

    assert summary.checklist.closed_shifts_count == 1
    assert summary.checklist.latest_closed_shift_id == today_shift.id
