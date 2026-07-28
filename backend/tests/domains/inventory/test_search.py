from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.repository import InventoryRepository


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

    rows, total = await InventoryRepository(db_session).search_with_expiry(
        catalog_id=item.id,
        branch_id=branch.id,
        branch_ids=None,
        expiry_status="red",
        show_empty=True,
        page=1,
        page_size=50,
    )

    assert total == 1
    assert rows[0]["id"] == empty_red.id
    assert rows[0]["expiry_status"] == "red"
