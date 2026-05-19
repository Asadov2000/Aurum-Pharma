"""Registers: CRUD + cross-tenant branch rejection."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService


async def test_register_crud(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))

    branch = await service.create_branch(tenant_id=tenant.id, fields={"name": "B"})

    r = await service.create_register(
        tenant_id=tenant.id,
        fields={"branch_id": branch.id, "name": "Касса №1", "printer_type": "thermal_80"},
    )
    assert r.branch_id == branch.id
    assert r.printer_type == "thermal_80"
    assert r.is_active is True

    listed = await service.list_registers(branch_id=branch.id)
    assert any(rr.id == r.id for rr in listed)

    updated = await service.update_register(r.id, fields={"name": "Касса №1 (новая)"})
    assert updated.name == "Касса №1 (новая)"

    deleted = await service.soft_delete_register(r.id)
    assert deleted.is_active is False


async def test_register_cross_tenant_branch_rejected(db_session: AsyncSession, make_tenant) -> None:
    """The service must refuse a branch_id that doesn't belong to the
    tenant we are scoping to, even on the support pool where RLS does
    not hide the cross-tenant row."""
    a = await make_tenant(name="A")
    b = await make_tenant(name="B")
    service = FoundationService(FoundationRepository(db_session))

    branch_in_b = await service.create_branch(tenant_id=b.id, fields={"name": "B-main"})

    with pytest.raises(BusinessRuleError):
        await service.create_register(
            tenant_id=a.id,
            fields={"branch_id": branch_in_b.id, "name": "wrong tenant"},
        )


async def test_register_on_inactive_branch_rejected(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))
    # Need two branches so we can deactivate one.
    keep = await service.create_branch(tenant_id=tenant.id, fields={"name": "keep"})
    dead = await service.create_branch(tenant_id=tenant.id, fields={"name": "dead"})
    await service.soft_delete_branch(dead.id)
    _ = keep  # silence unused-var lint

    with pytest.raises(BusinessRuleError):
        await service.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": dead.id, "name": "on-dead"},
        )
