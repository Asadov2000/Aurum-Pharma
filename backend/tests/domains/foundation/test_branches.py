"""Branches: CRUD, soft-delete, last-active guard."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.deps import CurrentUser
from app.core.errors import BusinessRuleError
from app.domains.foundation.models import Branch
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.router import update_branch as update_branch_route
from app.domains.foundation.schemas import BranchUpdate
from app.domains.foundation.service import FoundationService
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService


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


async def test_branch_route_preserves_explicit_null_when_clearing_receipt_header(
    db_session: AsyncSession,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    actor = await make_user(home_tenant_id=tenant.id)
    service = FoundationService(FoundationRepository(db_session))
    branch = await service.create_branch(
        tenant_id=tenant.id,
        fields={"name": "Main", "receipt_header": {"line1": "Аптека Сино"}},
    )
    user = CurrentUser(
        user_id=actor.id,
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
        permissions={"branches.update"},
        permission_scopes={"branches.update": None},
    )

    result = await update_branch_route(
        branch_id=branch.id,
        payload=BranchUpdate(receipt_header=None),
        user=user,
        service=service,
    )

    assert result.receipt_header is None


@pytest.mark.parametrize("field_name", ["name", "branch_type", "is_active"])
def test_branch_update_rejects_explicit_null_for_required_columns(field_name: str) -> None:
    with pytest.raises(ValidationError, match=f"{field_name} cannot be null"):
        BranchUpdate.model_validate({field_name: None})


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
    register = await service.create_register(
        tenant_id=tenant.id,
        fields={"branch_id": b1.id, "name": "A register"},
    )

    # Deactivating one is fine — another is still active. Its workstations
    # are disabled atomically so the UI cannot offer an unusable register.
    result = await service.soft_delete_branch(b1.id)
    assert result.is_active is False
    await db_session.refresh(register)
    assert register.is_active is False

    # Restoring a point must not silently restore each workstation: an owner
    # reviews those individually before the next shift.
    restored = await service.update_branch(b1.id, fields={"is_active": True})
    assert restored.is_active is True
    await db_session.refresh(register)
    assert register.is_active is False

    await service.soft_delete_branch(b1.id)

    # But the *new* last active branch cannot be touched.
    with pytest.raises(BusinessRuleError):
        await service.soft_delete_branch(b2.id)


async def test_branch_deactivation_rejects_open_shift_but_allows_closed_shift(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))
    pos = POSService(POSRepository(db_session))

    blocked = await service.create_branch(tenant_id=tenant.id, fields={"name": "Has shift"})
    _keep_active = await service.create_branch(tenant_id=tenant.id, fields={"name": "Keep active"})
    register = await service.create_register(
        tenant_id=tenant.id,
        fields={"branch_id": blocked.id, "name": "Register with open shift"},
    )
    cashier = await make_user(home_tenant_id=tenant.id)

    await pos.open_shift(
        tenant_id=tenant.id,
        register_id=register.id,
        opened_by_user_id=cashier.id,
        opening_cash=Decimal("0"),
    )

    with pytest.raises(BusinessRuleError, match="open shift"):
        await service.soft_delete_branch(blocked.id)
    with pytest.raises(BusinessRuleError, match="open shift"):
        await service.update_branch(blocked.id, fields={"is_active": False})

    _, impact = await service.get_branch_lifecycle_impact(blocked.id)
    assert impact.active_register_count == 1
    assert impact.open_shift_count == 1
    assert impact.active_assignment_count == 0
    assert impact.active_edge_node_count == 0

    await db_session.refresh(blocked)
    assert blocked.is_active is True

    open_shift = await pos.get_current_shift(user_id=cashier.id, register_id=register.id)
    assert open_shift is not None
    await pos.close_shift(
        shift_id=open_shift.id,
        closing_cash_actual=Decimal("0"),
        closed_by_user_id=cashier.id,
    )

    deleted = await service.soft_delete_branch(blocked.id)
    assert deleted.is_active is False


async def test_cannot_activate_register_in_inactive_branch(
    db_session: AsyncSession, make_tenant
) -> None:
    tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))
    branch = await service.create_branch(tenant_id=tenant.id, fields={"name": "Paused"})
    await service.create_branch(tenant_id=tenant.id, fields={"name": "Active"})
    register = await service.create_register(
        tenant_id=tenant.id,
        fields={"branch_id": branch.id, "name": "Paused register"},
    )

    await service.soft_delete_branch(branch.id)

    with pytest.raises(BusinessRuleError, match="inactive branch"):
        await service.update_register(register.id, fields={"is_active": True})


async def test_parallel_deactivation_keeps_one_active_branch(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    tenant_id = None
    branch_ids: list[UUID] = []
    try:
        async with factory.begin() as session:
            service = FoundationService(FoundationRepository(session))
            tenant = await service.create_tenant(
                payload={
                    "name": "Parallel branch lifecycle",
                    "contact_email": f"parallel-branch-{uuid4().hex[:10]}@aurum.tj",
                }
            )
            tenant_id = tenant.id
            first = await service.create_branch(tenant_id=tenant.id, fields={"name": "First"})
            second = await service.create_branch(tenant_id=tenant.id, fields={"name": "Second"})
            branch_ids = [first.id, second.id]

        async def deactivate(branch_id: UUID):
            async with factory.begin() as session:
                return await FoundationService(FoundationRepository(session)).soft_delete_branch(
                    branch_id
                )

        results = await asyncio.wait_for(
            asyncio.gather(
                *(deactivate(branch_id) for branch_id in branch_ids),
                return_exceptions=True,
            ),
            timeout=10,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, BusinessRuleError) for result in results) == 1

        async with factory() as session:
            active_count = await session.scalar(
                select(func.count())
                .select_from(Branch)
                .where(
                    Branch.tenant_id == tenant_id,
                    Branch.is_active.is_(True),
                )
            )
        assert active_count == 1
    finally:
        if tenant_id is not None:
            async with maintenance_engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
            async with db_engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.support_session', 'true', true)")
                )
                await connection.execute(
                    text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )


async def test_search_branches_filters_scope_status_and_tenant(
    db_session: AsyncSession,
    make_tenant,
) -> None:
    tenant = await make_tenant()
    other_tenant = await make_tenant()
    service = FoundationService(FoundationRepository(db_session))
    main = await service.create_branch(
        tenant_id=tenant.id,
        fields={
            "name": "Central Pharmacy",
            "address": "Dushanbe, Rudaki 10",
            "branch_type": "pharmacy",
            "license_number": "LIC-001",
        },
    )
    inactive = await service.create_branch(
        tenant_id=tenant.id,
        fields={"name": "North Kiosk", "branch_type": "kiosk"},
    )
    await service.create_branch(
        tenant_id=tenant.id,
        fields={"name": "South Post", "branch_type": "pharmacy_post"},
    )
    await service.soft_delete_branch(inactive.id)
    await service.create_branch(
        tenant_id=other_tenant.id,
        fields={
            "name": "Dushanbe Foreign",
            "address": "Dushanbe",
            "branch_type": "pharmacy",
        },
    )

    by_address, address_total = await service.search_branches(
        tenant_id=tenant.id,
        q="DUSHANBE",
    )
    assert address_total == 1
    assert by_address[0].id == main.id

    inactive_items, inactive_total = await service.search_branches(
        tenant_id=tenant.id,
        branch_type="kiosk",
        is_active=False,
    )
    assert inactive_total == 1
    assert inactive_items[0].id == inactive.id

    scoped, scoped_total = await service.search_branches(
        tenant_id=tenant.id,
        allowed_branch_ids={inactive.id},
        page=1,
        page_size=1,
    )
    assert scoped_total == 1
    assert [branch.id for branch in scoped] == [inactive.id]

    repeated, repeated_total = await service.search_branches(
        tenant_id=tenant.id,
        allowed_branch_ids={inactive.id},
        page=1,
        page_size=1,
    )
    assert repeated_total == scoped_total
    assert [branch.id for branch in repeated] == [branch.id for branch in scoped]
