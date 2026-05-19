"""write-off creates both write_off + batch_movement; trigger guards qty."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.service import InventoryService


async def test_write_off_decreases_qty(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item()
    batch = await make_batch(tenant_id=tenant.id, branch_id=branch.id, catalog_id=item.id, qty=10)
    service = InventoryService(InventoryRepository(db_session))

    wo = await service.write_off(
        batch_id=batch.id,
        qty=Decimal("3"),
        reason="damaged",
        comment=None,
        actor_id=None,
    )
    assert wo.qty == Decimal("3")

    # Re-read the batch — qty_remaining must be 7.
    refreshed = await service.get_batch(batch.id)
    assert refreshed.qty_remaining == Decimal("7.000")

    # Movement ledger has one entry of type write_off, qty_delta = -3.
    movements = await service.list_movements(batch.id)
    assert len(movements) == 1
    assert movements[0].movement_type == "write_off"
    assert movements[0].qty_delta == Decimal("-3.000")


async def test_write_off_more_than_available_blocked_by_trigger(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item()
    batch = await make_batch(tenant_id=tenant.id, branch_id=branch.id, catalog_id=item.id, qty=5)
    service = InventoryService(InventoryRepository(db_session))

    with pytest.raises(BusinessRuleError):
        await service.write_off(
            batch_id=batch.id,
            qty=Decimal("99"),
            reason="other",
            comment=None,
            actor_id=None,
        )


async def test_write_off_blocked_batch_rejected(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item()
    batch = await make_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=item.id,
        qty=10,
        is_blocked=True,
    )
    service = InventoryService(InventoryRepository(db_session))

    with pytest.raises(BusinessRuleError):
        await service.write_off(
            batch_id=batch.id,
            qty=Decimal("1"),
            reason="other",
            comment=None,
            actor_id=None,
        )
