"""Authorization gates for tenant API reads and POS receipt visibility."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.catalog.image_processing import CatalogImageVariants
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.roles.models import (
    Role,
    RolePermission,
    TenantMembership,
    TenantOwnership,
    UserAssignment,
)
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.service import SuppliersService
from app.main import app

SENSITIVE_READ_PATHS = [
    ("/api/v1/batches", "batches.view"),
    ("/api/v1/billing/invoices", "reports.view"),
    ("/api/v1/reports/sales-summary?from=2026-01-01&to=2026-01-31", "reports.view"),
]

DOMAIN_READ_PATHS = [
    ("/api/v1/catalog", 200),
    ("/api/v1/catalog/summary", 200),
    (f"/api/v1/catalog/{uuid4()}/image/{uuid4()}/thumbnail", 404),
    (f"/api/v1/catalog/import/{uuid4()}", 404),
    ("/api/v1/branches", 200),
    ("/api/v1/registers", 200),
    ("/api/v1/roles", 200),
    ("/api/v1/permissions", 200),
    ("/api/v1/onboarding/checklist", 200),
    ("/api/v1/onboarding/overview", 200),
    (f"/api/v1/shifts/current?register_id={uuid4()}", 200),
]


async def _override_db(db_session: AsyncSession) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override


async def _seed_scoped_batches(
    db_session: AsyncSession,
    *,
    tenant_id: UUID,
    branch_a_id: UUID,
    branch_b_id: UUID,
    suffix: str,
) -> tuple[UUID, UUID]:
    catalog = CatalogService(CatalogRepository(db_session))
    catalog_item = await catalog.create_item(
        tenant_id=tenant_id,
        fields={"brand_name": f"Scoped item {suffix}"},
    )
    inventory = InventoryRepository(db_session)
    batch_ids: list[UUID] = []
    for branch_id in (branch_a_id, branch_b_id):
        batch = await inventory.create_batch(
            tenant_id=tenant_id,
            branch_id=branch_id,
            catalog_id=catalog_item.id,
            expires_at=date.today() + timedelta(days=90),
            purchase_price=Decimal("5"),
            sale_price=Decimal("10"),
            qty_initial=Decimal("5"),
            qty_remaining=Decimal("5"),
        )
        batch_ids.append(batch.id)
    return batch_ids[0], batch_ids[1]


async def _assert_batch_scope(
    client: AsyncClient,
    *,
    headers: dict[str, str],
    visible_batch_id: UUID,
    forbidden_batch_id: UUID,
) -> None:
    batches_resp = await client.get("/api/v1/batches", headers=headers)
    assert batches_resp.status_code == 200
    assert [item["id"] for item in batches_resp.json()["items"]] == [str(visible_batch_id)]

    other_batch_resp = await client.get(
        f"/api/v1/batches/{forbidden_batch_id}",
        headers=headers,
    )
    assert other_batch_resp.status_code == 403


async def _assert_reporting_is_forbidden(
    client: AsyncClient,
    *,
    headers: dict[str, str],
) -> None:
    dashboard_resp = await client.get("/api/v1/dashboard/summary", headers=headers)
    billing_resp = await client.get("/api/v1/billing/invoices", headers=headers)
    assert dashboard_resp.status_code == 403
    assert billing_resp.status_code == 403


async def _assert_report_branch_scope(
    client: AsyncClient,
    *,
    headers: dict[str, str],
    visible_branch_name: str,
    forbidden_branch_id: UUID,
) -> None:
    today = date.today().isoformat()
    scoped_response = await client.get(
        f"/api/v1/reports/sales-summary?from={today}&to={today}",
        headers=headers,
    )
    assert scoped_response.status_code == 200
    assert scoped_response.json()["branch_name"] == visible_branch_name

    forbidden_response = await client.get(
        (
            f"/api/v1/reports/sales-summary?from={today}&to={today}"
            f"&branch_id={forbidden_branch_id}"
        ),
        headers=headers,
    )
    assert forbidden_response.status_code == 403


async def _seed_tenant_subjects(
    db_session: AsyncSession,
    *,
    admin_is_owner: bool = False,
) -> tuple[Tenant, AppUser, AppUser]:
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
        status="active",
    )
    db_session.add_all([regular, admin])
    await db_session.flush()
    await db_session.refresh(regular)
    await db_session.refresh(admin)
    regular_membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=regular.id,
        full_name=regular.full_name,
        status="active",
    )
    admin_membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=admin.id,
        full_name=admin.full_name,
        status="active",
    )
    db_session.add_all([regular_membership, admin_membership])
    await db_session.flush()
    if admin_is_owner:
        db_session.add(
            TenantOwnership(
                tenant_id=tenant.id,
                membership_id=admin_membership.id,
            )
        )
        await db_session.flush()
        repo = RolesRepository(db_session)
        owner_role = await RolesService(repo)._ensure_tenant_owner_role(tenant.id, admin.id)
        await repo.insert_assignment(
            user_id=admin.id,
            tenant_id=tenant.id,
            branch_id=None,
            role_id=owner_role.id,
            password_required=False,
        )
    return tenant, regular, admin


def _token(user: AppUser) -> str:
    return create_access_token(
        user.id,
        tenant_id=user.home_tenant_id,
        is_developer=False,
        is_administrator=False,
    )


async def _assign_permissions(
    db_session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    permission_codes: set[str],
    branch_id: UUID | None = None,
) -> None:
    membership = await db_session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
        )
    )
    if membership is None:
        user = await db_session.get(AppUser, user_id)
        assert user is not None
        membership = TenantMembership(
            tenant_id=tenant_id,
            user_id=user_id,
            full_name=user.full_name,
            status="active",
        )
        db_session.add(membership)
        await db_session.flush()
        await db_session.refresh(membership)

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
    db_session.add(
        UserAssignment(
            user_id=user_id,
            tenant_id=tenant_id,
            membership_id=membership.id,
            branch_id=branch_id,
            role_id=role.id,
        )
    )
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


async def _assert_incoming_update_contract(
    client: AsyncClient,
    *,
    db_session: AsyncSession,
    tenant_id: UUID,
    document_id: str,
    headers: dict[str, str],
    nick: str,
) -> None:
    clear_number_resp = await client.patch(
        f"/api/v1/incoming/{document_id}",
        headers=headers,
        json={"document_number": None},
    )
    assert clear_number_resp.status_code == 200
    assert clear_number_resp.json()["document_number"] is None

    reject_required_null_resp = await client.patch(
        f"/api/v1/incoming/{document_id}",
        headers=headers,
        json={"branch_id": None},
    )
    assert reject_required_null_resp.status_code == 422

    catalog_item = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=tenant_id,
        fields={"brand_name": f"Incoming Drug {nick}"},
    )
    invalid_precision_resp = await client.post(
        f"/api/v1/incoming/{document_id}/items",
        headers=headers,
        json={
            "catalog_id": str(catalog_item.id),
            "expires_at": (date.today() + timedelta(days=180)).isoformat(),
            "qty": "0.0001",
            "purchase_price": "1.999",
            "sale_price": "2.00",
        },
    )
    assert invalid_precision_resp.status_code == 422

    item_resp = await client.post(
        f"/api/v1/incoming/{document_id}/items",
        headers=headers,
        json={
            "catalog_id": str(catalog_item.id),
            "batch_number": "BATCH-TO-CLEAR",
            "manufactured_at": date.today().isoformat(),
            "expires_at": (date.today() + timedelta(days=180)).isoformat(),
            "qty": "1.000",
            "purchase_price": "1.99",
            "sale_price": "2.00",
        },
    )
    assert item_resp.status_code == 201

    clear_item_resp = await client.patch(
        f"/api/v1/incoming/{document_id}/items/{item_resp.json()['id']}",
        headers=headers,
        json={"batch_number": None, "manufactured_at": None},
    )
    assert clear_item_resp.status_code == 200
    assert clear_item_resp.json()["batch_number"] is None
    assert clear_item_resp.json()["manufactured_at"] is None


async def _assert_atomic_checkout_access(
    client: AsyncClient,
    *,
    peer_headers: dict[str, str],
    manager_headers: dict[str, str],
    checkout_operation_id: UUID,
    checkout_payload: dict[str, object],
) -> None:
    peer_checkout = await client.post(
        "/api/v1/sales/checkout",
        headers=peer_headers,
        json=checkout_payload,
    )
    assert peer_checkout.status_code == 403

    rx_checkout = await client.post(
        "/api/v1/sales/checkout",
        headers=manager_headers,
        json={
            **checkout_payload,
            "operation_id": str(uuid4()),
            "prescription": {"prescription_number": "RX-SEC"},
        },
    )
    assert rx_checkout.status_code == 403

    manager_checkout = await client.post(
        "/api/v1/sales/checkout",
        headers=manager_headers,
        json=checkout_payload,
    )
    assert manager_checkout.status_code == 201

    peer_recovery = await client.get(
        f"/api/v1/sales/operations/{checkout_operation_id}",
        headers=peer_headers,
    )
    assert peer_recovery.status_code == 403

    manager_recovery = await client.get(
        f"/api/v1/sales/operations/{checkout_operation_id}",
        headers=manager_headers,
    )
    assert manager_recovery.status_code == 200
    assert manager_recovery.json() == manager_checkout.json()


async def _assert_current_shift_visibility(
    client: AsyncClient,
    *,
    register_id: UUID,
    shift_id: UUID,
    peer_headers: dict[str, str],
    manager_headers: dict[str, str],
) -> None:
    peer_response = await client.get(
        f"/api/v1/shifts/current?register_id={register_id}",
        headers=peer_headers,
    )
    manager_response = await client.get(
        f"/api/v1/shifts/current?register_id={register_id}",
        headers=manager_headers,
    )

    assert peer_response.status_code == 200
    assert peer_response.json() is None
    assert manager_response.status_code == 200
    assert manager_response.json()["id"] == str(shift_id)


@pytest.mark.parametrize(("path", "permission_code"), SENSITIVE_READ_PATHS)
async def test_sensitive_reads_require_domain_permission(
    db_session: AsyncSession,
    client: AsyncClient,
    path: str,
    permission_code: str,
) -> None:
    await _override_db(db_session)
    try:
        tenant, regular, admin = await _seed_tenant_subjects(db_session)
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=admin.id,
            permission_codes={permission_code},
        )
        regular_token = _token(regular)
        admin_token = _token(admin)

        regular_resp = await client.get(path, headers={"Authorization": f"Bearer {regular_token}"})
        assert regular_resp.status_code == 403, path

        admin_resp = await client.get(path, headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_resp.status_code == 200, path
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_branch_scoped_user_sees_and_uses_only_assigned_branch(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _override_db(db_session)
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        pos = POSService(POSRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={"name": f"Branch Sec {nick}", "contact_email": f"branch-{nick}@aurum.tj"}
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        branch_a = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "A"})
        branch_b = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "B"})
        batch_a_id, batch_b_id = await _seed_scoped_batches(
            db_session,
            tenant_id=tenant.id,
            branch_a_id=branch_a.id,
            branch_b_id=branch_b.id,
            suffix=nick,
        )
        register_a = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": branch_a.id, "name": "A-1"},
        )
        register_b = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": branch_b.id, "name": "B-1"},
        )

        branch_user = AppUser(
            email=f"branch-user-{nick}@aurum.tj",
            full_name="Branch User",
            home_tenant_id=tenant.id,
            status="active",
        )
        peer = AppUser(
            email=f"branch-peer-{nick}@aurum.tj",
            full_name="Branch Peer",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add_all([branch_user, peer])
        await db_session.flush()
        await db_session.refresh(branch_user)
        await db_session.refresh(peer)
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=branch_user.id,
            branch_id=branch_a.id,
            permission_codes={
                "branches.view",
                "registers.view",
                "pos.shift_open",
                "pos.sell",
                "batches.view",
                "reports.view",
                "sales.view.tenant",
            },
        )

        headers = {"Authorization": f"Bearer {_token(branch_user)}"}

        branches_resp = await client.get("/api/v1/branches", headers=headers)
        assert branches_resp.status_code == 200
        assert [item["id"] for item in branches_resp.json()] == [str(branch_a.id)]

        other_branch_resp = await client.get(f"/api/v1/branches/{branch_b.id}", headers=headers)
        assert other_branch_resp.status_code == 403

        registers_resp = await client.get("/api/v1/registers", headers=headers)
        assert registers_resp.status_code == 200
        assert [item["id"] for item in registers_resp.json()] == [str(register_a.id)]

        other_registers_resp = await client.get(
            f"/api/v1/registers?branch_id={branch_b.id}",
            headers=headers,
        )
        assert other_registers_resp.status_code == 200
        assert other_registers_resp.json() == []

        await _assert_reporting_is_forbidden(client, headers=headers)

        await _assert_report_branch_scope(
            client,
            headers=headers,
            visible_branch_name="A",
            forbidden_branch_id=branch_b.id,
        )

        await _assert_batch_scope(
            client,
            headers=headers,
            visible_batch_id=batch_a_id,
            forbidden_batch_id=batch_b_id,
        )

        own_shift_resp = await client.post(
            "/api/v1/shifts/open",
            headers=headers,
            json={"register_id": str(register_a.id), "opening_cash": "0"},
        )
        assert own_shift_resp.status_code == 201

        other_shift_resp = await client.post(
            "/api/v1/shifts/open",
            headers=headers,
            json={"register_id": str(register_b.id), "opening_cash": "0"},
        )
        assert other_shift_resp.status_code == 403

        await pos.open_shift(
            tenant_id=tenant.id,
            register_id=register_b.id,
            opened_by_user_id=peer.id,
            opening_cash=Decimal("0"),
        )
        sale_a = await pos.create_sale(
            tenant_id=tenant.id,
            register_id=register_a.id,
            cashier_user_id=branch_user.id,
        )
        sale_b = await pos.create_sale(
            tenant_id=tenant.id,
            register_id=register_b.id,
            cashier_user_id=peer.id,
        )

        own_branch_sale_resp = await client.get(f"/api/v1/sales/{sale_a.id}", headers=headers)
        assert own_branch_sale_resp.status_code == 200

        other_branch_sale_resp = await client.get(f"/api/v1/sales/{sale_b.id}", headers=headers)
        assert other_branch_sale_resp.status_code == 403

        create_other_sale_resp = await client.post(
            "/api/v1/sales",
            headers=headers,
            json={"operation_id": str(uuid4()), "register_id": str(register_b.id)},
        )
        assert create_other_sale_resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_branch_scoped_incoming_user_cannot_use_other_branch(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _override_db(db_session)
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        suppliers = SuppliersService(SuppliersRepository(db_session))
        incoming = IncomingService(IncomingRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={
                "name": f"Incoming Sec {nick}",
                "contact_email": f"incoming-{nick}@aurum.tj",
            }
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        branch_a = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "A"})
        branch_b = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "B"})
        supplier = await suppliers.create_supplier(
            tenant_id=tenant.id,
            fields={"name": f"Supplier {nick}"},
        )
        branch_user = AppUser(
            email=f"incoming-user-{nick}@aurum.tj",
            full_name="Incoming User",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add(branch_user)
        await db_session.flush()
        await db_session.refresh(branch_user)
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=branch_user.id,
            branch_id=branch_a.id,
            permission_codes={"incoming.view", "incoming.create"},
        )

        other_doc = await incoming.create_document(
            tenant_id=tenant.id,
            fields={
                "branch_id": branch_b.id,
                "supplier_id": supplier.id,
                "document_date": date.today(),
                "document_number": f"OTHER-{nick}",
            },
        )

        headers = {"Authorization": f"Bearer {_token(branch_user)}"}
        branches_resp = await client.get("/api/v1/branches", headers=headers)
        assert branches_resp.status_code == 200
        assert [item["id"] for item in branches_resp.json()] == [str(branch_a.id)]

        own_payload = {
            "branch_id": str(branch_a.id),
            "supplier_id": str(supplier.id),
            "document_date": date.today().isoformat(),
            "document_number": f"OWN-{nick}",
        }
        own_resp = await client.post("/api/v1/incoming", headers=headers, json=own_payload)
        assert own_resp.status_code == 201
        own_doc_id = own_resp.json()["id"]
        await _assert_incoming_update_contract(
            client,
            db_session=db_session,
            tenant_id=tenant.id,
            document_id=own_doc_id,
            headers=headers,
            nick=nick,
        )

        other_create_resp = await client.post(
            "/api/v1/incoming",
            headers=headers,
            json={**own_payload, "branch_id": str(branch_b.id)},
        )
        assert other_create_resp.status_code == 403

        list_resp = await client.get("/api/v1/incoming", headers=headers)
        assert list_resp.status_code == 200
        list_payload = list_resp.json()
        assert [item["id"] for item in list_payload["items"]] == [own_doc_id]
        assert list_payload["items"][0]["branch_name"] == branch_a.name
        assert list_payload["items"][0]["supplier_name"] == supplier.name
        assert list_payload["total"] == 1
        assert list_payload["summary"] == {
            "all_count": 1,
            "draft_count": 1,
            "accepted_count": 0,
            "rejected_count": 0,
            "accepted_amount": "0",
            "currency": "TJS",
        }

        get_other_resp = await client.get(f"/api/v1/incoming/{other_doc.id}", headers=headers)
        assert get_other_resp.status_code == 403

        move_to_other_resp = await client.patch(
            f"/api/v1/incoming/{own_doc_id}",
            headers=headers,
            json={"branch_id": str(branch_b.id)},
        )
        assert move_to_other_resp.status_code == 403
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
        owner_required = path in {"/api/v1/roles", "/api/v1/permissions"}
        tenant, regular, admin = await _seed_tenant_subjects(
            db_session,
            admin_is_owner=owner_required,
        )
        if not owner_required:
            await _assign_permissions(
                db_session,
                tenant_id=tenant.id,
                user_id=admin.id,
                permission_codes={
                    "branches.view",
                    "catalog.create",
                    "catalog.view",
                    "pos.shift_open",
                    "registers.view",
                    "settings.update",
                    "users.view",
                },
            )
        regular_token = _token(regular)
        admin_token = _token(admin)

        regular_resp = await client.get(path, headers={"Authorization": f"Bearer {regular_token}"})
        assert regular_resp.status_code == 403, path

        admin_resp = await client.get(path, headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_resp.status_code == allowed_status, path
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_catalog_images_require_exact_permissions_and_tenant_scope(
    db_session: AsyncSession,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _override_db(db_session)
    try:
        tenant, viewer, editor = await _seed_tenant_subjects(db_session)
        other_tenant, other_viewer, _other_admin = await _seed_tenant_subjects(db_session)
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=viewer.id,
            permission_codes={"catalog.view"},
        )
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=editor.id,
            permission_codes={"catalog.view", "catalog.update"},
        )
        await _assign_permissions(
            db_session,
            tenant_id=other_tenant.id,
            user_id=other_viewer.id,
            permission_codes={"catalog.view"},
        )
        item = await CatalogService(CatalogRepository(db_session)).create_item(
            tenant_id=tenant.id,
            fields={"brand_name": "Protected image"},
        )
        processed = CatalogImageVariants(
            display=b"display-webp",
            thumbnail=b"thumbnail-webp",
            width=100,
            height=80,
            sha256="a" * 64,
        )
        catalog_router_module = importlib.import_module("app.domains.catalog.router")
        monkeypatch.setattr(
            catalog_router_module,
            "process_catalog_image",
            lambda _raw, _content_type: processed,
        )
        monkeypatch.setattr(
            catalog_router_module,
            "put_object",
            lambda *, object_name, data, content_type: f"aurum/{object_name}",
        )
        monkeypatch.setattr(
            catalog_router_module,
            "get_object",
            lambda _object_path: b"webp",
        )
        monkeypatch.setattr(
            catalog_router_module,
            "remove_object",
            lambda _object_path: None,
        )

        viewer_headers = {"Authorization": f"Bearer {_token(viewer)}"}
        editor_headers = {"Authorization": f"Bearer {_token(editor)}"}
        other_headers = {"Authorization": f"Bearer {_token(other_viewer)}"}
        forbidden_upload = await client.put(
            f"/api/v1/catalog/{item.id}/image",
            headers=viewer_headers,
            files={"file": ("medicine.png", b"raw", "image/png")},
        )
        assert forbidden_upload.status_code == 403

        uploaded = await client.put(
            f"/api/v1/catalog/{item.id}/image",
            headers=editor_headers,
            files={"file": ("medicine.png", b"raw", "image/png")},
        )
        assert uploaded.status_code == 200
        version = uploaded.json()["image_version"]

        visible = await client.get(
            f"/api/v1/catalog/{item.id}/image/{version}/thumbnail",
            headers=viewer_headers,
        )
        assert visible.status_code == 200
        assert visible.headers["content-type"] == "image/webp"
        assert visible.headers["x-content-type-options"] == "nosniff"

        hidden = await client.get(
            f"/api/v1/catalog/{item.id}/image/{version}/thumbnail",
            headers=other_headers,
        )
        assert hidden.status_code == 404
        forbidden_delete = await client.delete(
            f"/api/v1/catalog/{item.id}/image",
            headers=viewer_headers,
        )
        assert forbidden_delete.status_code == 403
        deleted = await client.delete(
            f"/api/v1/catalog/{item.id}/image",
            headers=editor_headers,
        )
        assert deleted.status_code == 200
        assert deleted.json()["image_version"] is None
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
        item = await _create_stocked_item(
            db_session,
            tenant_id=tenant.id,
            branch_id=branch.id,
        )
        sale = await pos.create_sale(
            tenant_id=tenant.id,
            register_id=register.id,
            cashier_user_id=cashier.id,
        )
        await pos.add_item(
            sale_id=sale.id,
            catalog_id=item.id,
            qty=Decimal("1"),
        )
        await pos.add_payment(
            sale_id=sale.id,
            payment_method="cash",
            amount=Decimal("10"),
        )
        sale = await pos.complete(sale_id=sale.id)

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
        peer_list = await client.get(
            f"/api/v1/sales?cashier_id={cashier.id}",
            headers={"Authorization": f"Bearer {_token(peer)}"},
        )
        assert peer_list.status_code == 200
        assert peer_list.json()["total"] == 0

        manager_resp = await client.get(
            f"/api/v1/sales/{sale.id}",
            headers={"Authorization": f"Bearer {_token(manager)}"},
        )
        assert manager_resp.status_code == 200
        manager_list = await client.get(
            f"/api/v1/sales?cashier_id={cashier.id}",
            headers={"Authorization": f"Bearer {_token(manager)}"},
        )
        assert manager_list.status_code == 200
        assert manager_list.json()["total"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_sales_list_keeps_each_capability_paired_with_its_branch_scope(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _override_db(db_session)
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        pos = POSService(POSRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={
                "name": f"Mixed Sales Scope {nick}",
                "contact_email": f"mixed-sales-{nick}@aurum.tj",
            }
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        own_branch = await foundation.create_branch(
            tenant_id=tenant.id,
            fields={"name": "Own branch"},
        )
        team_branch = await foundation.create_branch(
            tenant_id=tenant.id,
            fields={"name": "Team branch"},
        )
        own_register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": own_branch.id, "name": "Own register"},
        )
        hidden_register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": own_branch.id, "name": "Hidden peer register"},
        )
        team_register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": team_branch.id, "name": "Team register"},
        )

        viewer = AppUser(
            email=f"mixed-viewer-{nick}@aurum.tj",
            full_name="Mixed Viewer",
            home_tenant_id=tenant.id,
            status="active",
        )
        hidden_peer = AppUser(
            email=f"mixed-hidden-{nick}@aurum.tj",
            full_name="Hidden Peer",
            home_tenant_id=tenant.id,
            status="active",
        )
        visible_peer = AppUser(
            email=f"mixed-visible-{nick}@aurum.tj",
            full_name="Visible Peer",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add_all([viewer, hidden_peer, visible_peer])
        await db_session.flush()
        await db_session.refresh(viewer)
        await db_session.refresh(hidden_peer)
        await db_session.refresh(visible_peer)
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=viewer.id,
            permission_codes={"sales.view.own"},
            branch_id=own_branch.id,
        )
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=viewer.id,
            permission_codes={"sales.view.tenant"},
            branch_id=team_branch.id,
        )

        stocked_items = {
            own_branch.id: await _create_stocked_item(
                db_session,
                tenant_id=tenant.id,
                branch_id=own_branch.id,
            ),
            team_branch.id: await _create_stocked_item(
                db_session,
                tenant_id=tenant.id,
                branch_id=team_branch.id,
            ),
        }
        sales = []
        for register, cashier in (
            (own_register, viewer),
            (hidden_register, hidden_peer),
            (team_register, visible_peer),
        ):
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
            item = stocked_items[register.branch_id]
            await pos.add_item(
                sale_id=sale.id,
                catalog_id=item.id,
                qty=Decimal("1"),
            )
            await pos.add_payment(
                sale_id=sale.id,
                payment_method="cash",
                amount=Decimal("10"),
            )
            sales.append(await pos.complete(sale_id=sale.id))

        headers = {"Authorization": f"Bearer {_token(viewer)}"}
        combined = await client.get("/api/v1/sales", headers=headers)
        assert combined.status_code == 200
        assert {item["id"] for item in combined.json()["items"]} == {
            str(sales[0].id),
            str(sales[2].id),
        }

        hidden_filter = await client.get(
            f"/api/v1/sales?cashier_id={hidden_peer.id}",
            headers=headers,
        )
        assert hidden_filter.status_code == 200
        assert hidden_filter.json()["total"] == 0

        visible_filter = await client.get(
            f"/api/v1/sales?cashier_id={visible_peer.id}",
            headers=headers,
        )
        assert visible_filter.status_code == 200
        assert {item["id"] for item in visible_filter.json()["items"]} == {str(sales[2].id)}
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_tenant_sales_view_does_not_expand_pos_sell_branch_scope(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _override_db(db_session)
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        pos = POSService(POSRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={
                "name": f"POS Capability Scope {nick}",
                "contact_email": f"pos-capability-{nick}@aurum.tj",
            }
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        allowed_branch = await foundation.create_branch(
            tenant_id=tenant.id,
            fields={"name": "Allowed POS branch"},
        )
        denied_branch = await foundation.create_branch(
            tenant_id=tenant.id,
            fields={"name": "Denied POS branch"},
        )
        denied_register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": denied_branch.id, "name": "Denied register"},
        )
        item = await _create_stocked_item(
            db_session,
            tenant_id=tenant.id,
            branch_id=denied_branch.id,
        )

        cashier = AppUser(
            email=f"scoped-cashier-{nick}@aurum.tj",
            full_name="Scoped Cashier",
            home_tenant_id=tenant.id,
            status="active",
        )
        manager = AppUser(
            email=f"scoped-manager-{nick}@aurum.tj",
            full_name="Scoped Manager",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add_all([cashier, manager])
        await db_session.flush()
        await db_session.refresh(cashier)
        await db_session.refresh(manager)
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=manager.id,
            permission_codes={"pos.sell"},
            branch_id=allowed_branch.id,
        )
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=manager.id,
            permission_codes={"sales.view.tenant"},
        )

        await pos.open_shift(
            tenant_id=tenant.id,
            register_id=denied_register.id,
            opened_by_user_id=cashier.id,
            opening_cash=Decimal("0"),
        )
        draft = await pos.create_sale(
            tenant_id=tenant.id,
            register_id=denied_register.id,
            cashier_user_id=cashier.id,
        )

        response = await client.post(
            f"/api/v1/sales/{draft.id}/items",
            headers={"Authorization": f"Bearer {_token(manager)}"},
            json={"operation_id": str(uuid4()), "catalog_id": str(item.id), "qty": "1"},
        )
        assert response.status_code == 403
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
                "pos.manage_sales",
                "pos.manage_shifts",
                "pos.sell",
                "pos.shift_close",
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
            json={"operation_id": str(uuid4()), "catalog_id": str(item.id), "qty": "1"},
        )
        assert add_item_resp.status_code == 403

        update_item_resp = await client.patch(
            f"/api/v1/sales/{sale.id}/items/{item_id}",
            headers=peer_headers,
            json={"operation_id": str(uuid4()), "qty": "2"},
        )
        assert update_item_resp.status_code == 403

        delete_item_resp = await client.request(
            "DELETE",
            f"/api/v1/sales/{sale.id}/items/{item_id}",
            headers=peer_headers,
            json={"operation_id": str(uuid4())},
        )
        assert delete_item_resp.status_code == 403

        payment_resp = await client.post(
            f"/api/v1/sales/{sale.id}/payments",
            headers=peer_headers,
            json={
                "operation_id": str(uuid4()),
                "payment_method": "cash",
                "amount": "10.00",
            },
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

        checkout_operation_id = uuid4()
        checkout_payload: dict[str, object] = {
            "operation_id": str(checkout_operation_id),
            "register_id": str(register.id),
            "draft_sale_id": str(sale.id),
            "items": [{"catalog_id": str(item.id), "qty": "1"}],
            "payments": [{"payment_method": "cash", "amount": "10.00"}],
        }
        manager_headers = {"Authorization": f"Bearer {_token(manager)}"}
        await _assert_current_shift_visibility(
            client,
            register_id=register.id,
            shift_id=shift.id,
            peer_headers=peer_headers,
            manager_headers=manager_headers,
        )
        await _assert_atomic_checkout_access(
            client,
            peer_headers=peer_headers,
            manager_headers=manager_headers,
            checkout_operation_id=checkout_operation_id,
            checkout_payload=checkout_payload,
        )

        close_resp = await client.post(
            f"/api/v1/shifts/{shift.id}/close",
            headers=peer_headers,
            json={"closing_cash_actual": "0"},
        )
        assert close_resp.status_code == 403

        manager_resp = await client.post(
            f"/api/v1/shifts/{shift.id}/close",
            headers=manager_headers,
            json={"closing_cash_actual": "0"},
        )
        assert manager_resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_readonly_tenant_blocks_pos_mutations(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _override_db(db_session)
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        pos = POSService(POSRepository(db_session))
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={"name": f"Readonly POS {nick}", "contact_email": f"ro-pos-{nick}@aurum.tj"}
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
        register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": branch.id, "name": "Cashbox"},
        )
        item = await _create_stocked_item(db_session, tenant_id=tenant.id, branch_id=branch.id)

        cashier = AppUser(
            email=f"readonly-cashier-{nick}@aurum.tj",
            full_name="Readonly Cashier",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add(cashier)
        await db_session.flush()
        await db_session.refresh(cashier)
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=cashier.id,
            permission_codes={
                "pos.shift_open",
                "pos.shift_close",
                "pos.sell",
                "pos.handle_prescription",
                "pos.refund",
            },
        )

        shift = await pos.open_shift(
            tenant_id=tenant.id,
            register_id=register.id,
            opened_by_user_id=cashier.id,
            opening_cash=Decimal("0"),
        )
        draft_sale = await pos.create_sale(
            tenant_id=tenant.id,
            register_id=register.id,
            cashier_user_id=cashier.id,
        )
        draft_items, _ = await pos.add_item(
            sale_id=draft_sale.id,
            catalog_id=item.id,
            qty=Decimal("1"),
        )

        completed_sale = await pos.create_sale(
            tenant_id=tenant.id,
            register_id=register.id,
            cashier_user_id=cashier.id,
        )
        completed_items, _ = await pos.add_item(
            sale_id=completed_sale.id,
            catalog_id=item.id,
            qty=Decimal("1"),
        )
        await pos.add_payment(
            sale_id=completed_sale.id,
            payment_method="cash",
            amount=Decimal("10.00"),
        )
        await pos.complete(sale_id=completed_sale.id)

        await foundation.update_tenant(tenant.id, fields={"status": "readonly"})

        headers = {"Authorization": f"Bearer {_token(cashier)}"}
        readonly_requests: list[tuple[str, str, dict[str, object] | None]] = [
            (
                "POST",
                "/api/v1/shifts/open",
                {"register_id": str(register.id), "opening_cash": "0"},
            ),
            (
                "POST",
                f"/api/v1/shifts/{shift.id}/close",
                {"closing_cash_actual": "0"},
            ),
            (
                "POST",
                "/api/v1/sales",
                {"operation_id": str(uuid4()), "register_id": str(register.id)},
            ),
            (
                "POST",
                f"/api/v1/sales/{draft_sale.id}/items",
                {"operation_id": str(uuid4()), "catalog_id": str(item.id), "qty": "1"},
            ),
            (
                "PATCH",
                f"/api/v1/sales/{draft_sale.id}/items/{draft_items[0].id}",
                {"operation_id": str(uuid4()), "qty": "2"},
            ),
            (
                "DELETE",
                f"/api/v1/sales/{draft_sale.id}/items/{draft_items[0].id}",
                {"operation_id": str(uuid4())},
            ),
            (
                "POST",
                f"/api/v1/sales/{draft_sale.id}/payments",
                {"payment_method": "cash", "amount": "10.00"},
            ),
            ("POST", f"/api/v1/sales/{draft_sale.id}/complete", None),
            (
                "POST",
                "/api/v1/sales/checkout",
                {
                    "operation_id": str(uuid4()),
                    "register_id": str(register.id),
                    "draft_sale_id": str(draft_sale.id),
                    "items": [{"catalog_id": str(item.id), "qty": "1"}],
                    "payments": [{"payment_method": "cash", "amount": "10.00"}],
                },
            ),
            (
                "POST",
                f"/api/v1/sales/{draft_sale.id}/prescription",
                {"prescription_number": "RX-READONLY"},
            ),
            (
                "POST",
                f"/api/v1/sales/{completed_sale.id}/refund",
                {
                    "items": [{"sale_item_id": str(completed_items[0].id), "qty": "1"}],
                    "reason": "readonly guard",
                },
            ),
        ]

        for method, path, payload in readonly_requests:
            if payload is None:
                response = await client.request(method, path, headers=headers)
            else:
                response = await client.request(method, path, headers=headers, json=payload)
            assert response.status_code == 422, f"{method} {path}: {response.text}"
            body = response.json()
            assert body["error"]["code"] == "business_rule_violation"
            assert body["error"]["details"] == {"status": "readonly"}
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_electronic_refund_confirmation_requires_separate_permission(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _override_db(db_session)
    try:
        foundation = FoundationService(FoundationRepository(db_session))
        repo = POSRepository(db_session)
        pos = POSService(repo)
        nick = uuid4().hex[:8]
        tenant = await foundation.create_tenant(
            payload={
                "name": f"Refund approval {nick}",
                "contact_email": f"refund-approval-{nick}@aurum.tj",
            }
        )
        await foundation.update_tenant(tenant.id, fields={"status": "active"})
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
        register = await foundation.create_register(
            tenant_id=tenant.id,
            fields={"branch_id": branch.id, "name": "Cashbox"},
        )
        catalog_item = await _create_stocked_item(
            db_session,
            tenant_id=tenant.id,
            branch_id=branch.id,
        )
        cashier = AppUser(
            email=f"refund-cashier-{nick}@aurum.tj",
            full_name="Refund Cashier",
            home_tenant_id=tenant.id,
            status="active",
        )
        approver = AppUser(
            email=f"refund-approver-{nick}@aurum.tj",
            full_name="Refund Approver",
            home_tenant_id=tenant.id,
            status="active",
        )
        db_session.add_all([cashier, approver])
        await db_session.flush()
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=cashier.id,
            permission_codes={"pos.refund"},
        )
        await _assign_permissions(
            db_session,
            tenant_id=tenant.id,
            user_id=approver.id,
            permission_codes={"pos.refund_external_confirm"},
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
        sale_items, _ = await pos.add_item(
            sale_id=sale.id,
            catalog_id=catalog_item.id,
            qty=Decimal("1"),
        )
        await repo.insert_payment(
            tenant_id=tenant.id,
            sale_id=sale.id,
            payment_method="card",
            amount=Decimal("10"),
        )
        sale = await pos.complete(sale_id=sale.id)

        cashier_headers = {"Authorization": f"Bearer {_token(cashier)}"}
        create_response = await client.post(
            f"/api/v1/sales/{sale.id}/refund-attempts",
            headers=cashier_headers,
            json={
                "operation_id": str(uuid4()),
                "items": [{"sale_item_id": str(sale_items[0].id), "qty": "1"}],
            },
        )
        assert create_response.status_code == 201
        attempt_id = create_response.json()["id"]
        confirmation = {
            "confirmations": [
                {
                    "payment_method": "card",
                    "terminal_id": "TERM-SEC",
                    "document_number": f"REFUND-{nick}",
                }
            ]
        }

        denied = await client.post(
            f"/api/v1/pos/refund-attempts/{attempt_id}/confirm",
            headers=cashier_headers,
            json=confirmation,
        )
        assert denied.status_code == 403

        approved = await client.post(
            f"/api/v1/pos/refund-attempts/{attempt_id}/confirm",
            headers={"Authorization": f"Bearer {_token(approver)}"},
            json=confirmation,
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "confirmed"
    finally:
        app.dependency_overrides.pop(get_db, None)
