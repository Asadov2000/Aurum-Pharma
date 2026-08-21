"""FEFO selection with an unconditional expired-batch exclusion."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.service import InventoryService


async def test_fefo_returns_earliest_first(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item(expired_sale_mode="strict")
    later = await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=5,
        expires_in_days=120,
    )
    earlier = await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=5,
        expires_in_days=30,
    )
    service = InventoryService(InventoryRepository(db_session))

    sel = await service.find_batches_fefo(
        tenant_id=tenant.id,
        catalog_id=item.id,
        branch_id=branch.id,
        qty_needed=Decimal("3"),
    )
    assert sel.total_picked == Decimal("3")
    assert sel.requires_warning is False
    assert sel.picks[0].batch.id == earlier.id
    # We didn't need to dip into the later one.
    assert all(p.batch.id != later.id for p in sel.picks)


async def test_fefo_splits_across_batches(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item(expired_sale_mode="strict")
    await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=3,
        expires_in_days=30,
    )
    await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=5,
        expires_in_days=90,
    )
    service = InventoryService(InventoryRepository(db_session))

    sel = await service.find_batches_fefo(
        tenant_id=tenant.id,
        catalog_id=item.id,
        branch_id=branch.id,
        qty_needed=Decimal("6"),
    )
    assert sel.total_picked == Decimal("6")
    assert len(sel.picks) == 2
    assert sel.picks[0].qty == Decimal("3")  # exhausted the earlier batch
    assert sel.picks[1].qty == Decimal("3")  # took 3 of 5 from the next one


async def test_fefo_excludes_expired(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item(expired_sale_mode="strict")
    await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=10,
        expires_in_days=-5,  # expired
    )
    service = InventoryService(InventoryRepository(db_session))

    sel = await service.find_batches_fefo(
        tenant_id=tenant.id,
        catalog_id=item.id,
        branch_id=branch.id,
        qty_needed=Decimal("1"),
    )
    assert sel.picks == []
    assert sel.total_picked == Decimal("0")


async def test_fefo_excludes_batch_expiring_today(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item(expired_sale_mode="strict")
    await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=10,
        expires_in_days=0,
    )
    service = InventoryService(InventoryRepository(db_session))

    sel = await service.find_batches_fefo(
        tenant_id=tenant.id,
        catalog_id=item.id,
        branch_id=branch.id,
        qty_needed=Decimal("1"),
    )
    assert sel.picks == []
    assert sel.total_picked == Decimal("0")
    assert sel.requires_warning is False


async def test_fefo_excludes_blocked(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item(expired_sale_mode="strict")
    await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=10,
        expires_in_days=10,
        is_blocked=True,
    )
    fresh = await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=5,
        expires_in_days=60,
    )
    service = InventoryService(InventoryRepository(db_session))

    sel = await service.find_batches_fefo(
        tenant_id=tenant.id,
        catalog_id=item.id,
        branch_id=branch.id,
        qty_needed=Decimal("2"),
    )
    assert len(sel.picks) == 1
    assert sel.picks[0].batch.id == fresh.id
