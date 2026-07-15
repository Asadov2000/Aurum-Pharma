"""GET /api/v1/auth/me."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import Role, RolePermission, UserAssignment


async def test_me_without_token_returns_401(auth_client: AsyncClient) -> None:
    response = await auth_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "authentication_required"


async def test_me_with_valid_token_returns_user(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="me@aurum.tj", full_name="Me User")
    token = create_access_token(
        user.id,
        tenant_id=user.home_tenant_id,
        is_developer=user.is_developer,
        is_administrator=user.is_administrator,
    )

    response = await auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "me@aurum.tj"
    assert body["full_name"] == "Me User"
    assert body["is_developer"] is False
    assert body["is_administrator"] is False
    assert body["level"] == 4


async def test_me_with_garbage_token_returns_401(auth_client: AsyncClient) -> None:
    response = await auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.real.jwt"}
    )
    assert response.status_code == 401


async def test_me_rejects_token_after_user_is_blocked(
    auth_client: AsyncClient,
    make_user,
) -> None:
    user = await make_user(email="blocked-me@aurum.tj", status="blocked")
    token = create_access_token(
        user.id,
        tenant_id=user.home_tenant_id,
        is_developer=False,
        is_administrator=False,
    )

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


async def test_me_rejects_stale_support_claims(
    auth_client: AsyncClient,
    make_user,
) -> None:
    user = await make_user(email="stale-support@aurum.tj")
    token = create_access_token(
        user.id,
        tenant_id=user.home_tenant_id,
        is_developer=True,
        is_administrator=False,
    )

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


async def test_me_level_comes_from_assigned_role_not_permission_heuristic(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={"name": "Level Tenant", "contact_email": "level-tenant@aurum.tj"}
    )
    user = await make_user(
        email="level-role@aurum.tj",
        full_name="Level Role",
        home_tenant_id=tenant.id,
    )
    role = Role(
        tenant_id=tenant.id,
        name="Level 4 assigner",
        level=4,
        is_system=False,
    )
    db_session.add(role)
    await db_session.flush()
    await db_session.refresh(role)
    db_session.add(RolePermission(role_id=role.id, permission_code="roles.assign"))
    db_session.add(UserAssignment(user_id=user.id, tenant_id=tenant.id, role_id=role.id))
    await db_session.flush()

    token = create_access_token(
        user.id,
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
    )

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["permissions"] == ["roles.assign"]
    assert body["level"] == 4


async def test_me_ignores_assignments_to_inactive_roles(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={"name": "Inactive Role Tenant", "contact_email": "inactive-role@aurum.tj"}
    )
    user = await make_user(
        email="inactive-role-user@aurum.tj",
        home_tenant_id=tenant.id,
    )
    role = Role(
        tenant_id=tenant.id,
        name="Inactive owner",
        level=3,
        is_system=False,
        is_active=False,
    )
    db_session.add(role)
    await db_session.flush()
    await db_session.refresh(role)
    db_session.add(RolePermission(role_id=role.id, permission_code="users.invite"))
    db_session.add(UserAssignment(user_id=user.id, tenant_id=tenant.id, role_id=role.id))
    await db_session.flush()

    token = create_access_token(
        user.id,
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
    )

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["permissions"] == []
    assert body["branch_assignments"] == {}
    assert body["level"] == 4
