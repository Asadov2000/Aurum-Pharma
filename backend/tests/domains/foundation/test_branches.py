"""Branches: CRUD, soft-delete, last-active guard."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService


async def test_branch_crud(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))

    # Create
    b1 = await service.create_branch(
        tenant_id=tenant.id,
        fields={"name": "Main", "branch_type": "pharmacy"},
    )
    assert b1.name == "Main"
    assert b1.is_active is True

    # List
    branches = await service.list_branches()
    assert any(b.id == b1.id for b in branches)

    # Get
    fetched = await service.get_branch(b1.id)
    assert fetched.id == b1.id

    # Update
    updated = await service.update_branch(b1.id, fields={"address": "ул. Главная 1"})
    assert updated.address == "ул. Главная 1"


async def test_cannot_deactivate_last_active_branch(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))

    # Only one active branch
    b1 = await service.create_branch(tenant_id=tenant.id, fields={"name": "Only one"})

    with pytest.raises(BusinessRuleError):
        await service.update_branch(b1.id, fields={"is_active": False})

    with pytest.raises(BusinessRuleError):
        await service.soft_delete_branch(b1.id)


async def test_can_deactivate_one_when_two_exist(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))

    b1 = await service.create_branch(tenant_id=tenant.id, fields={"name": "A"})
    b2 = await service.create_branch(tenant_id=tenant.id, fields={"name": "B"})

    # Deactivating one is fine — another is still active.
    result = await service.soft_delete_branch(b1.id)
    assert result.is_active is False

    # But the *new* last active branch cannot be touched.
    with pytest.raises(BusinessRuleError):
        await service.soft_delete_branch(b2.id)
