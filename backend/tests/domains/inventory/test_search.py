from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domains.inventory.expiry import build_expiry_boundaries
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.service import InventoryService


async def test_show_empty_can_be_combined_with_expiry_filter(
    db_session: AsyncSession,
    make_branch_and_item,
    make_batch,
) -> None:
    tenant, branch, item = await make_branch_and_item()
    empty_red = await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        expires_in_days=15,
    )
    empty_red.qty_remaining = Decimal("0")
    await db_session.flush()

    rows, summary = await InventoryRepository(db_session).search_with_expiry(
        catalog_id=item.id,
        branch_id=branch.id,
        branch_ids=None,
        expiry_status="red",
        batch_number=None,
        is_blocked=None,
        show_empty=True,
        page=1,
        page_size=50,
        tenant_id=tenant.id,
        boundaries=build_expiry_boundaries(date.today()),
    )

    assert summary.total == 1
    assert summary.total_qty == Decimal("0")
    assert rows[0].batch.id == empty_red.id
    assert rows[0].branch_name == branch.name
    assert rows[0].catalog_name == item.brand_name
    assert rows[0].expiry_status == "red"


async def test_direct_batch_read_is_explicitly_tenant_scoped(
    db_session: AsyncSession,
    make_branch_and_item,
    make_batch,
) -> None:
    tenant_a, branch_a, item_a = await make_branch_and_item()
    tenant_b, _branch_b, _item_b = await make_branch_and_item()
    batch_a = await make_batch(
        tenant_id=tenant_a.id,
        branch_id=branch_a.id,
        catalog_id=item_a.id,
    )
    service = InventoryService(InventoryRepository(db_session))

    with pytest.raises(NotFoundError, match="Batch not found"):
        await service.get_batch(batch_a.id, tenant_id=tenant_b.id)


async def test_blocked_batch_is_counted_as_requiring_attention(
    db_session: AsyncSession,
    make_branch_and_item,
    make_batch,
) -> None:
    tenant, branch, item = await make_branch_and_item()
    blocked = await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        expires_in_days=730,
    )
    blocked.is_blocked = True
    await db_session.flush()

    _rows, summary = await InventoryRepository(db_session).search_with_expiry(
        catalog_id=item.id,
        branch_id=branch.id,
        branch_ids=None,
        expiry_status=None,
        batch_number=None,
        is_blocked=None,
        show_empty=False,
        page=1,
        page_size=50,
        tenant_id=tenant.id,
        boundaries=build_expiry_boundaries(date.today()),
    )

    assert summary.attention_count == 1
    assert summary.blocked_count == 1
