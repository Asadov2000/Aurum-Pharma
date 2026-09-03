"""Catalog search stock enrichment: available stock per result for a branch,
computed in a single grouped query (no N+1). Without branch_id → no stock."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository


async def _branch_id(db: AsyncSession, tenant_id: UUID) -> UUID:
    foundation = FoundationService(FoundationRepository(db))
    branch = await foundation.create_branch(tenant_id=tenant_id, fields={"name": "Main"})
    return branch.id


async def _stocked_item(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    brand: str,
    qty: int,
    expires_in_days: int = 90,
    expires_at: date | None = None,
) -> UUID:
    catalog = CatalogService(CatalogRepository(db))
    inv = InventoryRepository(db)
    item = await catalog.create_item(tenant_id=tenant_id, fields={"brand_name": brand})
    batch = await inv.create_batch(
        tenant_id=tenant_id,
        branch_id=branch_id,
        catalog_id=item.id,
        expires_at=expires_at or date.today() + timedelta(days=expires_in_days),
        purchase_price=Decimal("1.00"),
        sale_price=Decimal("2.00"),
        qty_initial=Decimal(str(qty)),
        qty_remaining=Decimal("0"),
    )
    await inv.insert_movement(
        tenant_id=tenant_id,
        batch_id=batch.id,
        movement_type="incoming",
        qty_delta=Decimal(str(qty)),
        source_table=None,
        source_id=None,
    )
    return item.id


async def test_search_returns_stock_available_for_branch(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    branch_id = await _branch_id(db_session, tenant.id)
    brand = f"Стоктест {tenant.id}"
    item_id = await _stocked_item(
        db_session, tenant_id=tenant.id, branch_id=branch_id, brand=brand, qty=40
    )
    service = CatalogService(CatalogRepository(db_session))

    _items, _total, stock = await service.search(
        q=brand,
        category=None,
        dispensing_type=None,
        page=1,
        page_size=50,
        branch_id=branch_id,
        tenant_id=tenant.id,
    )
    assert stock.get(item_id) == Decimal("40")

    # No branch → empty stock map (existing behaviour; field serializes null).
    _items2, _total2, stock2 = await service.search(
        q=brand,
        category=None,
        dispensing_type=None,
        page=1,
        page_size=50,
        branch_id=None,
        tenant_id=tenant.id,
    )
    assert stock2 == {}


async def test_stock_by_catalog_is_a_single_grouped_query(
    db_session: AsyncSession, db_connection: AsyncConnection, make_tenant
) -> None:
    tenant = await make_tenant()
    branch_id = await _branch_id(db_session, tenant.id)
    ids = [
        await _stocked_item(
            db_session, tenant_id=tenant.id, branch_id=branch_id, brand=f"N1 {tenant.id}", qty=10
        ),
        await _stocked_item(
            db_session, tenant_id=tenant.id, branch_id=branch_id, brand=f"N2 {tenant.id}", qty=7
        ),
        await _stocked_item(
            db_session, tenant_id=tenant.id, branch_id=branch_id, brand=f"N3 {tenant.id}", qty=7
        ),
    ]
    await db_session.flush()

    # Count cursor executions during the aggregation: exactly one grouped query
    # for N catalog ids, not one per item.
    count = {"n": 0}

    def _before(*_args: object) -> None:
        count["n"] += 1

    sync_conn = db_connection.sync_connection
    event.listen(sync_conn, "before_cursor_execute", _before)
    try:
        stock = await CatalogRepository(db_session).stock_by_catalog(
            tenant_id=tenant.id,
            branch_id=branch_id,
            catalog_ids=ids,
            today=date.today(),
        )
    finally:
        event.remove(sync_conn, "before_cursor_execute", _before)

    assert count["n"] == 1
    assert stock[ids[0]] == Decimal("10")
    assert stock[ids[1]] == Decimal("7")
    assert stock[ids[2]] == Decimal("7")


async def test_stock_available_excludes_expired_batches(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    branch_id = await _branch_id(db_session, tenant.id)
    brand = f"Expired stock {tenant.id}"
    item_id = await _stocked_item(
        db_session,
        tenant_id=tenant.id,
        branch_id=branch_id,
        brand=brand,
        qty=12,
        expires_in_days=-1,
    )

    _items, _total, stock = await CatalogService(CatalogRepository(db_session)).search(
        q=brand,
        category=None,
        dispensing_type=None,
        page=1,
        page_size=50,
        branch_id=branch_id,
        tenant_id=tenant.id,
    )

    assert stock[item_id] == 0


@pytest.mark.parametrize(
    ("timezone_name", "expected_stock"),
    [("Asia/Dushanbe", 0), ("America/Los_Angeles", 12)],
)
async def test_stock_available_uses_tenant_local_calendar_date(
    db_session: AsyncSession, make_tenant, timezone_name: str, expected_stock: int
) -> None:
    tenant = await make_tenant()
    settings = await FoundationRepository(db_session).get_settings(tenant.id)
    assert settings is not None
    settings.report_timezone = timezone_name
    await db_session.flush()
    branch_id = await _branch_id(db_session, tenant.id)
    brand = f"Midnight stock {tenant.id}"
    item_id = await _stocked_item(
        db_session,
        tenant_id=tenant.id,
        branch_id=branch_id,
        brand=brand,
        qty=12,
        expires_at=date(2026, 5, 1),
    )
    instant = datetime(2026, 4, 30, 20, 30, tzinfo=UTC)
    service = CatalogService(CatalogRepository(db_session), now=lambda: instant)

    _items, _total, stock = await service.search(
        q=brand,
        category=None,
        dispensing_type=None,
        page=1,
        page_size=50,
        branch_id=branch_id,
        tenant_id=tenant.id,
    )

    assert stock[item_id] == expected_stock

    picker_items, picker_stock = await service.search_picker(
        q=brand, branch_id=branch_id, limit=10, tenant_id=tenant.id
    )
    assert [item.id for item in picker_items] == [item_id]
    assert picker_stock[item_id] == expected_stock
