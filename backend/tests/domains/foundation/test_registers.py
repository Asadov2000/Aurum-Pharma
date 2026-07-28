"""Registers: CRUD + cross-tenant branch rejection."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


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


async def test_register_deactivation_rejects_open_shift_but_allows_closed_shift(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))
    pos = POSService(POSRepository(db_session))
    branch = await service.create_branch(tenant_id=tenant.id, fields={"name": "B"})
    register = await service.create_register(
        tenant_id=tenant.id,
        fields={"branch_id": branch.id, "name": "Register with open shift"},
    )
    cashier = await make_user(home_tenant_id=tenant.id)

    await pos.open_shift(
        tenant_id=tenant.id,
        register_id=register.id,
        opened_by_user_id=cashier.id,
        opening_cash=Decimal("0"),
    )

    with pytest.raises(BusinessRuleError, match="open shift"):
        await service.soft_delete_register(register.id)
    with pytest.raises(BusinessRuleError, match="open shift"):
        await service.update_register(register.id, fields={"is_active": False})

    await db_session.refresh(register)
    assert register.is_active is True

    open_shift = await pos.get_current_shift(user_id=cashier.id, register_id=register.id)
    assert open_shift is not None
    await pos.close_shift(
        shift_id=open_shift.id,
        closing_cash_actual=Decimal("0"),
        closed_by_user_id=cashier.id,
    )

    deleted = await service.soft_delete_register(register.id)
    assert deleted.is_active is False


async def test_search_registers_filters_scope_status_and_tenant(
    db_session: AsyncSession,
    make_tenant,
) -> None:
    tenant = await make_tenant()
    other_tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))
    branch_a = await service.create_branch(tenant_id=tenant.id, fields={"name": "A"})
    branch_b = await service.create_branch(tenant_id=tenant.id, fields={"name": "B"})
    other_branch = await service.create_branch(
        tenant_id=other_tenant.id,
        fields={"name": "Other"},
    )
    front = await service.create_register(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch_a.id,
            "name": "Front Desk",
            "printer_type": "thermal_80",
        },
    )
    await service.create_register(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch_a.id,
            "name": "Backup",
            "printer_type": "browser",
        },
    )
    await service.create_register(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch_b.id,
            "name": "Front Branch B",
            "printer_type": "thermal_80",
        },
    )
    inactive = await service.create_register(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch_a.id,
            "name": "Old Front",
            "printer_type": "thermal_80",
        },
    )
    await service.soft_delete_register(inactive.id)
    await service.create_register(
        tenant_id=other_tenant.id,
        fields={
            "branch_id": other_branch.id,
            "name": "Front Other Tenant",
            "printer_type": "thermal_80",
        },
    )

    filtered, filtered_total = await service.search_registers(
        tenant_id=tenant.id,
        q="FRONT",
        branch_id=branch_a.id,
        printer_type="thermal_80",
        is_active=True,
    )
    assert filtered_total == 1
    assert filtered[0].id == front.id

    inactive_items, inactive_total = await service.search_registers(
        tenant_id=tenant.id,
        is_active=False,
    )
    assert inactive_total == 1
    assert inactive_items[0].id == inactive.id

    scoped, scoped_total = await service.search_registers(
        tenant_id=tenant.id,
        allowed_branch_ids={branch_a.id},
        page=1,
        page_size=2,
    )
    assert scoped_total == 3
    assert len(scoped) == 2

    repeated, repeated_total = await service.search_registers(
        tenant_id=tenant.id,
        allowed_branch_ids={branch_a.id},
        page=1,
        page_size=2,
    )
    assert repeated_total == scoped_total
    assert [register.id for register in repeated] == [register.id for register in scoped]

    outside_scope, outside_total = await service.search_registers(
        tenant_id=tenant.id,
        branch_id=branch_b.id,
        allowed_branch_ids={branch_a.id},
    )
    assert outside_scope == []
    assert outside_total == 0
