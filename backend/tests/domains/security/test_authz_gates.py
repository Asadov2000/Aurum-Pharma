"""Authorization gates for tenant API reads and POS receipt visibility."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.roles.models import Role, RolePermission, UserAssignment
from app.main import app

REPORT_READ_PATHS = ["/api/v1/batches", "/api/v1/billing/invoices"]

DOMAIN_READ_PATHS = [
    ("/api/v1/catalog", 200),
    (f"/api/v1/catalog/import/{uuid4()}", 404),
    ("/api/v1/branches", 200),
    ("/api/v1/registers", 200),
    ("/api/v1/roles", 200),
    ("/api/v1/permissions", 200),
    ("/api/v1/onboarding/checklist", 200),
    (f"/api/v1/shifts/current?register_id={uuid4()}", 200),
]


async def _override_db(db_session: AsyncSession) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override


async def _seed_tenant_subjects(db_session: AsyncSession) -> tuple[Tenant, AppUser, AppUser]:
    foundation = FoundationService(FoundationRepository(db_session))
    nick = uuid4().hex[:8]
    tenant = await foundation.create_tenant(
        payload={"name": f"Sec {nick}", "contact_email": f"s-{nick}@aurum.tj"}
    )
    await foundation.update_tenant(tenant.id, fields={"status": "active"})

    regular = AppUser(
        email=f"regular-{nick}@aurum.tj",
        full_name="Regular",
        home_tenant_id=tenant.id,
        status="active",
    )
    admin = AppUser(
        email=f"admin-{nick}@aurum.tj",
        full_name="Admin",
        home_tenant_id=tenant.id,
        is_administrator=True,
        status="active",
    )
    db_session.add_all([regular, admin])
    await db_session.flush()
    await db_session.refresh(regular)
    await db_session.refresh(admin)
    return tenant, regular, admin


def _token(user: AppUser, *, is_administrator: bool = False) -> str:
    return create_access_token(
        user.id,
        tenant_id=user.home_tenant_id,
        is_developer=False,
        is_administrator=is_administrator,
    )


async def _assign_permissions(
    db_session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    permission_codes: set[str],
) -> None:
    role = Role(
        tenant_id=tenant_id,
        name=f"sec-role-{uuid4().hex[:8]}",
        level=4,
        is_system=False,
    )
    db_session.add(role)
    await db_session.flush()
    await db_session.refresh(role)
    for code in permission_codes:
        db_session.add(RolePermission(role_id=role.id, permission_code=code))
    db_session.add(UserAssignment(user_id=user_id, tenant_id=tenant_id, role_id=role.id))
    await db_session.flush()


@pytest.mark.parametrize("path", REPORT_READ_PATHS)
async def test_sensitive_reads_require_reports_view(
    db_session: AsyncSession, client: AsyncClient, path: str
) -> None:
    await _override_db(db_session)
    try:
        _tenant, regular, admin = await _seed_tenant_subjects(db_session)
        regular_token = _token(regular)
        admin_token = _token(admin, is_administrator=True)

        regular_resp = await client.get(path, headers={"Authorization": f"Bearer {regular_token}"})
        assert regular_resp.status_code == 403, path

        admin_resp = await client.get(path, headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_resp.status_code == 200, path
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize(("path", "allowed_status"), DOMAIN_READ_PATHS)
async def test_tenant_reads_require_domain_permission(
    db_session: AsyncSession,
    client: AsyncClient,
    path: str,
    allowed_status: int,
) -> None:
    await _override_db(db_session)
    try:
        _tenant, regular, admin = await _seed_tenant_subjects(db_session)
        regular_token = _token(regular)
        admin_token = _token(admin, is_administrator=True)

        regular_resp = await client.get(path, headers={"Authorization": f"Bearer {regular_token}"})
        assert regular_resp.status_code == 403, path

        admin_resp = await client.get(path, headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_resp.status_code == allowed_status, path
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_cashier_cannot_view_another_cashiers_sale(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _override_db(db_session)
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        pos = POSService(POSRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={"name": f"Sales Sec {nick}", "contact_email": f"sales-{nick}@aurum.tj"}
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
        register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": branch.id, "name": "Cashbox"},
        )
        cashier = AppUser(
            email=f"cashier-{nick}@aurum.tj",
            full_name="Cashier",
            home_tenant_id=tenant.id,
            status="active",
        )
        peer = AppUser(
            email=f"peer-{nick}@aurum.tj",
            full_name="Peer",
            home_tenant_id=tenant.id,
            status="active",
        )
        manager = AppUser(
            email=f"manager-{nick}@aurum.tj",
            full_name="Manager",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add_all([cashier, peer, manager])
        await db_session.flush()
        await db_session.refresh(cashier)
        await db_session.refresh(peer)
        await db_session.refresh(manager)
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=cashier.id,
            permission_codes={"sales.view.own"},
        )
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=peer.id,
            permission_codes={"sales.view.own"},
        )
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=manager.id,
            permission_codes={"sales.view.tenant"},
        )

        await pos.open_shift(
            tenant_id=tenant.id,
            register_id=register.id,
            opened_by_user_id=cashier.id,
            opening_cash=Decimal("0"),
        )
        sale = await pos.create_sale(
            tenant_id=tenant.id,
            register_id=register.id,
            cashier_user_id=cashier.id,
        )

        cashier_resp = await client.get(
            f"/api/v1/sales/{sale.id}",
            headers={"Authorization": f"Bearer {_token(cashier)}"},
        )
        assert cashier_resp.status_code == 200

        peer_resp = await client.get(
            f"/api/v1/sales/{sale.id}",
            headers={"Authorization": f"Bearer {_token(peer)}"},
        )
        assert peer_resp.status_code == 403

        manager_resp = await client.get(
            f"/api/v1/sales/{sale.id}",
            headers={"Authorization": f"Bearer {_token(manager)}"},
        )
        assert manager_resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
