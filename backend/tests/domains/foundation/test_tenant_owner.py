"""POST /api/v1/admin/tenants/{id}/owner — support onboards a tenant's owner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import (
    Role,
    TenantMembership,
    TenantOwnership,
    UserAssignment,
)
from app.domains.roles.repository import RolesRepository
from app.main import app


def _support_token(user: AppUser, *, developer: bool = True) -> str:
    return create_access_token(
        user.id,
        tenant_id=None,
        is_developer=developer,
        is_administrator=not developer,
    )


async def _make_tenant(db: AsyncSession):  # type: ignore[no-untyped-def]
    foundation = FoundationService(FoundationRepository(db))
    nick = uuid4().hex[:8]
    return await foundation.create_tenant(
        payload={"name": f"Owner {nick}", "contact_email": f"t-{nick}@aurum.tj"}
    )


async def _make_user(db: AsyncSession, **flags: bool) -> AppUser:
    nick = uuid4().hex[:8]
    u = AppUser(email=f"u-{nick}@aurum.tj", full_name="Actor", status="active", **flags)
    db.add(u)
    await db.flush()
    await db.refresh(u)
    return u


async def test_developer_provisions_owner(db_session: AsyncSession, client: AsyncClient) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await _make_tenant(db_session)
        dev = await _make_user(db_session, is_developer=True)
        token = _support_token(dev, developer=True)

        resp = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/owner",
            json={"email": "vladelec@shifo.tj", "full_name": "Владелец Аптеки"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["email"] == "vladelec@shifo.tj"
        assert body["home_tenant_id"] == str(tenant.id)

        # User created, scoped to the target tenant, no platform flags.
        owner = await db_session.get(AppUser, body["user_id"])
        assert owner is not None
        assert owner.home_tenant_id == tenant.id
        assert owner.is_developer is False
        assert owner.is_administrator is False

        # «Владелец» role instantiated as a tenant role and assigned.
        role = await db_session.get(Role, body["role_id"])
        assert role is not None
        assert role.tenant_id == tenant.id
        assert role.name == "Владелец"
        assert role.is_system is False
        assert role.level == 3

        assignment = (
            (
                await db_session.execute(
                    select(UserAssignment).where(
                        UserAssignment.user_id == owner.id,
                        UserAssignment.tenant_id == tenant.id,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert assignment is not None
        assert assignment.role_id == role.id
        assert assignment.is_active is True
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_support_creates_pending_member_at_frontend_contract_path(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await _make_tenant(db_session)
        administrator = await _make_user(db_session, is_administrator=True)
        token = _support_token(administrator, developer=False)
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "email": "pending-member@shifo.tj",
            "full_name": "Pending Member",
        }

        response = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/members",
            json=payload,
            headers=headers,
        )
        legacy_path = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/memberships",
            json={
                "email": "must-not-be-created@shifo.tj",
                "full_name": "Legacy",
            },
            headers=headers,
        )

        assert response.status_code == 201, response.text
        assert legacy_path.status_code == 404
        body = response.json()
        account = await db_session.get(AppUser, body["user_id"])
        membership = await db_session.get(
            TenantMembership,
            body["membership_id"],
        )
        assert account is not None
        assert account.status == "invited"
        assert account.home_tenant_id == tenant.id
        assert membership is not None
        assert membership.status == "pending"
        assert membership.tenant_id == tenant.id
        assignment_count = await db_session.scalar(
            select(func.count())
            .select_from(UserAssignment)
            .where(UserAssignment.membership_id == membership.id)
        )
        assert assignment_count == 0
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_owner_role_matches_template_permissions(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await _make_tenant(db_session)
        dev = await _make_user(db_session, is_developer=True)
        token = _support_token(dev)

        resp = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/owner",
            json={"email": "owner2@shifo.tj", "full_name": "О"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        role_id = resp.json()["role_id"]

        repo = RolesRepository(db_session)
        template = await repo.get_template_by_slug("owner")
        assert template is not None
        tpl_codes = sorted(await repo.get_template_permissions(template.id))
        role_codes = sorted(await repo.get_role_permissions(role_id))
        assert role_codes == tpl_codes
        assert role_codes  # non-empty
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_non_support_actor_forbidden(db_session: AsyncSession, client: AsyncClient) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await _make_tenant(db_session)
        seller = await _make_user(db_session)  # no flags
        seller.home_tenant_id = tenant.id
        db_session.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=seller.id,
                full_name=seller.full_name,
                status="active",
            )
        )
        await db_session.flush()
        token = create_access_token(
            seller.id, tenant_id=tenant.id, is_developer=False, is_administrator=False
        )

        resp = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/owner",
            json={"email": "x@shifo.tj", "full_name": "X"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_duplicate_email_conflict(db_session: AsyncSession, client: AsyncClient) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await _make_tenant(db_session)
        dev = await _make_user(db_session, is_developer=True)
        token = _support_token(dev)
        payload = {"email": "dup-owner@shifo.tj", "full_name": "Дубль"}

        first = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/owner",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/owner",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 409, second.text
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_bootstrap_endpoint_rejects_second_active_owner(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await _make_tenant(db_session)
        dev = await _make_user(db_session, is_developer=True)
        token = _support_token(dev)

        r1 = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/owner",
            json={"email": "owner-a@shifo.tj", "full_name": "A"},
            headers={"Authorization": f"Bearer {token}"},
        )
        r2 = await client.post(
            f"/api/v1/admin/tenants/{tenant.id}/owner",
            json={"email": "owner-b@shifo.tj", "full_name": "B"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 201
        assert r2.status_code == 409
        ownership_count = (
            await db_session.execute(
                select(func.count())
                .select_from(TenantOwnership)
                .where(
                    TenantOwnership.tenant_id == tenant.id,
                    TenantOwnership.is_active.is_(True),
                )
            )
        ).scalar_one()
        assert ownership_count == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
