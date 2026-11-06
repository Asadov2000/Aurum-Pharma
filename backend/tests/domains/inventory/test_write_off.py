"""write-off creates both write_off + batch_movement; trigger guards qty."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
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
        operation_id=uuid4(),
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
            operation_id=uuid4(),
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
            operation_id=uuid4(),
            qty=Decimal("1"),
            reason="other",
            comment=None,
            actor_id=None,
        )


async def test_write_off_retry_is_idempotent(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item()
    batch = await make_batch(tenant_id=tenant.id, branch_id=branch.id, catalog_id=item.id, qty=10)
    service = InventoryService(InventoryRepository(db_session))
    operation_id = uuid4()

    first = await service.write_off(
        batch_id=batch.id,
        operation_id=operation_id,
        qty=Decimal("2.5"),
        reason="damaged",
        comment="Broken package",
        actor_id=None,
    )
    retried = await service.write_off(
        batch_id=batch.id,
        operation_id=operation_id,
        qty=Decimal("2.5"),
        reason="damaged",
        comment="Broken package",
        actor_id=None,
    )

    assert retried.id == first.id == operation_id
    assert (await service.get_batch(batch.id)).qty_remaining == Decimal("7.500")
    assert len(await service.list_movements(batch.id)) == 1


@pytest.mark.parametrize("changed_field", [None, "batch_id", "qty", "reason", "comment"])
async def test_write_off_retry_checks_branch_before_payload(
    db_session: AsyncSession, make_branch_and_item, make_batch, changed_field: str | None
) -> None:
    tenant, foreign_branch, item = await make_branch_and_item()
    own_branch = await FoundationService(FoundationRepository(db_session)).create_branch(
        tenant_id=tenant.id, fields={"name": "Assigned branch"}
    )
    foreign_batch = await make_batch(
        tenant_id=tenant.id, branch_id=foreign_branch.id, catalog_id=item.id, qty=10
    )
    own_batch = await make_batch(
        tenant_id=tenant.id, branch_id=own_branch.id, catalog_id=item.id, qty=10
    )
    service = InventoryService(InventoryRepository(db_session))
    operation_id = uuid4()
    await service.write_off(
        batch_id=foreign_batch.id,
        operation_id=operation_id,
        qty=Decimal("1"),
        reason="damaged",
        comment=None,
        actor_id=None,
        tenant_id=tenant.id,
        allowed_branch_ids={foreign_branch.id},
    )

    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.write_off(
            batch_id=own_batch.id if changed_field == "batch_id" else foreign_batch.id,
            operation_id=operation_id,
            qty=Decimal("2") if changed_field == "qty" else Decimal("1"),
            reason="other" if changed_field == "reason" else "damaged",
            comment="Changed" if changed_field == "comment" else None,
            actor_id=None,
            tenant_id=tenant.id,
            allowed_branch_ids={own_branch.id},
        )

    assert (await service.get_batch(foreign_batch.id)).qty_remaining == Decimal("9.000")
    assert (await service.get_batch(own_batch.id)).qty_remaining == Decimal("10.000")
    assert len(await service.list_movements(foreign_batch.id)) == 1
    assert not await service.list_movements(own_batch.id)


async def test_write_off_race_rechecks_existing_branch(
    db_session: AsyncSession, make_branch_and_item, make_batch, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, foreign_branch, item = await make_branch_and_item()
    own_branch = await FoundationService(FoundationRepository(db_session)).create_branch(
        tenant_id=tenant.id, fields={"name": "Assigned branch"}
    )
    foreign_batch = await make_batch(
        tenant_id=tenant.id, branch_id=foreign_branch.id, catalog_id=item.id, qty=10
    )
    own_batch = await make_batch(
        tenant_id=tenant.id, branch_id=own_branch.id, catalog_id=item.id, qty=10
    )
    repo = InventoryRepository(db_session)
    service = InventoryService(repo)
    operation_id = uuid4()
    existing = await service.write_off(
        batch_id=foreign_batch.id,
        operation_id=operation_id,
        qty=Decimal("1"),
        reason="damaged",
        comment=None,
        actor_id=None,
        tenant_id=tenant.id,
    )
    # Another branch can win the operation ID between the lookup and insert.
    monkeypatch.setattr(repo, "get_write_off", AsyncMock(side_effect=[None, existing]))
    monkeypatch.setattr(
        repo,
        "insert_write_off",
        AsyncMock(side_effect=IntegrityError("write-off insert", {}, Exception("duplicate key"))),
    )

    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.write_off(
            batch_id=own_batch.id,
            operation_id=operation_id,
            qty=Decimal("1"),
            reason="damaged",
            comment=None,
            actor_id=None,
            tenant_id=tenant.id,
            allowed_branch_ids={own_branch.id},
        )

    assert (await service.get_batch(own_batch.id)).qty_remaining == Decimal("10.000")
    assert not await service.list_movements(own_batch.id)


async def test_write_off_lookup_is_explicitly_tenant_scoped(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item()
    other_tenant, _, _ = await make_branch_and_item()
    batch = await make_batch(tenant_id=tenant.id, branch_id=branch.id, catalog_id=item.id, qty=10)
    repo = InventoryRepository(db_session)
    operation_id = uuid4()
    existing = await InventoryService(repo).write_off(
        batch_id=batch.id,
        operation_id=operation_id,
        qty=Decimal("1"),
        reason="damaged",
        comment=None,
        actor_id=None,
        tenant_id=tenant.id,
    )

    assert await repo.get_write_off(operation_id, tenant_id=tenant.id) is existing
    assert await repo.get_write_off(operation_id, tenant_id=other_tenant.id) is None


async def test_write_off_operation_id_cannot_be_reused_with_different_data(
    db_session: AsyncSession, make_branch_and_item, make_batch
) -> None:
    tenant, branch, item = await make_branch_and_item()
    batch = await make_batch(tenant_id=tenant.id, branch_id=branch.id, catalog_id=item.id, qty=10)
    service = InventoryService(InventoryRepository(db_session))
    operation_id = uuid4()
    await service.write_off(
        batch_id=batch.id,
        operation_id=operation_id,
        qty=Decimal("1"),
        reason="damaged",
        comment=None,
        actor_id=None,
    )

    with pytest.raises(BusinessRuleError, match="reused with different data"):
        await service.write_off(
            batch_id=batch.id,
            operation_id=operation_id,
            qty=Decimal("2"),
            reason="damaged",
            comment=None,
            actor_id=None,
        )
