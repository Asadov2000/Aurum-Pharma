"""Authorization gates for tenant API reads and POS receipt visibility."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository
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


async def _create_stocked_item(db_session: AsyncSession, *, tenant_id: UUID, branch_id: UUID):
    catalog = CatalogService(CatalogRepository(db_session))
    inventory = InventoryRepository(db_session)
    item = await catalog.create_item(
        tenant_id=tenant_id,
        fields={"brand_name": f"Sec Drug {uuid4().hex[:8]}"},
    )
    batch = await inventory.create_batch(
        tenant_id=tenant_id,
        branch_id=branch_id,
        catalog_id=item.id,
        expires_at=date.today() + timedelta(days=180),
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("10.00"),
        qty_initial=Decimal("10"),
        qty_remaining=Decimal("0"),
    )
    await inventory.insert_movement(
        tenant_id=tenant_id,
        batch_id=batch.id,
        movement_type="incoming",
        qty_delta=Decimal("10"),
    )
    return item


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


async def test_cashier_cannot_mutate_another_cashiers_pos_work(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _override_db(db_session)
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        pos = POSService(POSRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={"name": f"POS Sec {nick}", "contact_email": f"pos-{nick}@aurum.tj"}
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
        register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": branch.id, "name": "Cashbox"},
        )
        item = await _create_stocked_item(db_session, tenant_id=tenant.id, branch_id=branch.id)

        cashier = AppUser(
            email=f"draft-cashier-{nick}@aurum.tj",
            full_name="Draft Cashier",
            home_tenant_id=tenant.id,
            status="active",
        )
        peer = AppUser(
            email=f"draft-peer-{nick}@aurum.tj",
            full_name="Draft Peer",
            home_tenant_id=tenant.id,
            status="active",
        )
        manager = AppUser(
            email=f"draft-manager-{nick}@aurum.tj",
            full_name="Draft Manager",
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
            user_id=peer.id,
            permission_codes={
                "pos.sell",
                "pos.shift_close",
                "pos.handle_prescription",
                "sales.view.own",
            },
        )
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=manager.id,
            permission_codes={
                "pos.sell",
                "pos.shift_close",
                "pos.handle_prescription",
                "sales.view.tenant",
            },
        )

        shift = await pos.open_shift(
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
        created_items, _ = await pos.add_item(
            sale_id=sale.id,
            catalog_id=item.id,
            qty=Decimal("1"),
        )
        item_id = created_items[0].id
        peer_headers = {"Authorization": f"Bearer {_token(peer)}"}

        add_item_resp = await client.post(
            f"/api/v1/sales/{sale.id}/items",
            headers=peer_headers,
            json={"catalog_id": str(item.id), "qty": "1"},
        )
        assert add_item_resp.status_code == 403

        update_item_resp = await client.patch(
            f"/api/v1/sales/{sale.id}/items/{item_id}",
            headers=peer_headers,
            json={"qty": "2"},
        )
        assert update_item_resp.status_code == 403

        delete_item_resp = await client.delete(
            f"/api/v1/sales/{sale.id}/items/{item_id}",
            headers=peer_headers,
        )
        assert delete_item_resp.status_code == 403

        payment_resp = await client.post(
            f"/api/v1/sales/{sale.id}/payments",
            headers=peer_headers,
            json={"payment_method": "cash", "amount": "10.00"},
        )
        assert payment_resp.status_code == 403

        prescription_resp = await client.post(
            f"/api/v1/sales/{sale.id}/prescription",
            headers=peer_headers,
            json={"prescription_number": "RX-SEC"},
        )
        assert prescription_resp.status_code == 403

        complete_resp = await client.post(
            f"/api/v1/sales/{sale.id}/complete",
            headers=peer_headers,
        )
        assert complete_resp.status_code == 403

        close_resp = await client.post(
            f"/api/v1/shifts/{shift.id}/close",
            headers=peer_headers,
            json={"closing_cash_actual": "0"},
        )
        assert close_resp.status_code == 403

        manager_resp = await client.post(
            f"/api/v1/shifts/{shift.id}/close",
            headers={"Authorization": f"Bearer {_token(manager)}"},
            json={"closing_cash_actual": "0"},
        )
        assert manager_resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
