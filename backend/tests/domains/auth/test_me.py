"""GET /api/v1/auth/me."""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, encode_token
from app.domains.foundation.models import Branch
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import (
    TenantMembership,
    UserAssignment,
)
from tests.auth_helpers import create_support_access_token, create_tenant_access_token
from tests.platform_access_helpers import create_test_platform_user
from tests.role_version_helpers import create_published_test_role, provision_test_owner


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
    token = await create_tenant_access_token(db_session, user)

    response = await auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["email"] == "me@aurum.tj"
    assert body["full_name"] == "Me User"
    assert body["is_developer"] is False
    assert body["is_administrator"] is False
    assert body["is_tenant_owner"] is False
    assert body["level"] == 4
    assert body["platform_capabilities"] == []


async def test_me_keeps_platform_capabilities_separate_from_tenant_permissions(
    auth_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    administrator = await create_test_platform_user(
        db_session,
        access_kind="administrator",
    )
    token = await create_support_access_token(db_session, administrator)

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["permissions"] == []
    assert "platform.tenants.view" in body["platform_capabilities"]
    assert "platform.sync.manage" not in body["platform_capabilities"]


async def test_platform_capability_lookup_rejects_another_active_session(
    db_session: AsyncSession,
) -> None:
    first = await create_test_platform_user(db_session, access_kind="developer")
    second = await create_test_platform_user(db_session, access_kind="developer")
    first_token = await create_support_access_token(db_session, first)
    second_token = await create_support_access_token(db_session, second)
    first_session_id = decode_access_token(first_token)["sid"]
    second_session_id = decode_access_token(second_token)["sid"]

    with pytest.raises(DBAPIError, match="outside the request identity"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("""
                    SELECT code
                    FROM public.lookup_active_platform_capabilities(
                      :user_id,
                      :session_id
                    )
                    """),
                {
                    "user_id": first.id,
                    "session_id": second_session_id,
                },
            )

    assert first_session_id != second_session_id


async def test_me_with_garbage_token_returns_401(auth_client: AsyncClient) -> None:
    response = await auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.real.jwt"}
    )
    assert response.status_code == 401


async def test_me_rejects_legacy_token_without_session(
    auth_client: AsyncClient,
    make_user,
) -> None:
    user = await make_user(email="legacy-sessionless@aurum.tj")
    token = encode_token(
        str(user.id),
        expires_in=timedelta(minutes=5),
        extra={
            "tenant_id": str(user.home_tenant_id),
            "is_developer": False,
            "is_administrator": False,
        },
    )

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


async def test_me_rejects_token_after_user_is_blocked(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="blocked-me@aurum.tj", status="blocked")
    token = await create_tenant_access_token(db_session, user)

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


async def test_me_rejects_stale_support_claims(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="stale-support@aurum.tj")
    token = await create_tenant_access_token(db_session, user, is_developer=True)

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    "membership_status",
    ["pending", "suspended", "offboarded"],
)
async def test_me_rejects_inactive_tenant_membership(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    membership_status: str,
) -> None:
    tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
        payload={
            "name": f"Inactive me {membership_status}",
            "contact_email": f"inactive-me-{membership_status}@aurum.tj",
        }
    )
    user = await make_user(
        email=f"me-{membership_status}@aurum.tj",
        home_tenant_id=tenant.id,
    )
    db_session.add(
        TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            full_name=user.full_name,
            status=membership_status,
        )
    )
    await db_session.flush()
    token = await create_tenant_access_token(db_session, user, tenant_id=tenant.id)

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


async def test_me_reports_active_tenant_ownership(
    auth_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
        payload={
            "name": "Owner identity",
            "contact_email": "owner-identity@aurum.tj",
        }
    )
    owner, _membership, _ownership, _role = await provision_test_owner(
        db_session,
        tenant_id=tenant.id,
        email="me-owner@aurum.tj",
        full_name="Owner",
    )
    token = await create_tenant_access_token(db_session, owner, tenant_id=tenant.id)

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["is_tenant_owner"] is True
    assert response.json()["level"] == 3


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
    role = await create_published_test_role(
        db_session,
        tenant_id=tenant.id,
        name="Level 4 stock operator",
        permission_codes=["batches.create"],
        level=4,
    )
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        full_name=user.full_name,
        status="active",
    )
    db_session.add(membership)
    await db_session.flush()
    await db_session.refresh(membership)
    db_session.add(
        UserAssignment(
            user_id=user.id,
            tenant_id=tenant.id,
            membership_id=membership.id,
            role_id=role.id,
        )
    )
    await db_session.flush()
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    await db_session.execute(text("SELECT set_config('app.support_access_session_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.auth_session_id', '', true)"))
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user.id)},
    )

    token = await create_tenant_access_token(db_session, user, tenant_id=tenant.id)

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["permissions"] == ["batches.create"]
    assert body["permission_scopes"] == {"batches.create": None}
    assert body["level"] == 4


async def test_me_exposes_exact_branch_permission_scope(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={"name": "Scoped Tenant", "contact_email": "scoped-me@aurum.tj"}
    )
    branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Рудаки"})
    user = await make_user(email="scoped-user@aurum.tj", home_tenant_id=tenant.id)
    role = await create_published_test_role(
        db_session,
        tenant_id=tenant.id,
        name="Scoped cashier",
        permission_codes=["pos.sell"],
        level=4,
    )
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        full_name=user.full_name,
        status="active",
    )
    db_session.add(membership)
    await db_session.flush()
    db_session.add(
        UserAssignment(
            user_id=user.id,
            tenant_id=tenant.id,
            membership_id=membership.id,
            role_id=role.id,
            branch_id=branch.id,
        )
    )
    await db_session.flush()
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    await db_session.execute(text("SELECT set_config('app.support_access_session_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.auth_session_id', '', true)"))
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user.id)},
    )

    token = await create_tenant_access_token(db_session, user, tenant_id=tenant.id)
    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["permission_scopes"] == {"pos.sell": [str(branch.id)]}


async def test_me_ignores_inactive_assignments(
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
    role = await create_published_test_role(
        db_session,
        tenant_id=tenant.id,
        name="Inactive catalog viewer",
        permission_codes=["catalog.view"],
        level=3,
    )
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        full_name=user.full_name,
        status="active",
    )
    db_session.add(membership)
    await db_session.flush()
    await db_session.refresh(membership)
    db_session.add(
        UserAssignment(
            user_id=user.id,
            tenant_id=tenant.id,
            membership_id=membership.id,
            role_id=role.id,
            is_active=False,
        )
    )
    await db_session.flush()
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    await db_session.execute(text("SELECT set_config('app.support_access_session_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.auth_session_id', '', true)"))
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user.id)},
    )

    token = await create_tenant_access_token(db_session, user, tenant_id=tenant.id)

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["permissions"] == []
    assert body["branch_assignments"] == {}
    assert body["level"] == 4


async def test_me_ignores_assignments_from_inactive_branches(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={"name": "Closed Branch Tenant", "contact_email": "closed-branch@aurum.tj"}
    )
    user = await make_user(
        email="closed-branch-user@aurum.tj",
        home_tenant_id=tenant.id,
    )
    role = await create_published_test_role(
        db_session,
        tenant_id=tenant.id,
        name="Closed branch cashier",
        permission_codes=["pos.sell"],
        level=4,
    )
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        full_name=user.full_name,
        status="active",
    )
    branch = Branch(tenant_id=tenant.id, name="Closed", is_active=False)
    db_session.add_all([membership, branch])
    await db_session.flush()
    db_session.add(
        UserAssignment(
            user_id=user.id,
            tenant_id=tenant.id,
            membership_id=membership.id,
            role_id=role.id,
            branch_id=branch.id,
        )
    )
    await db_session.flush()
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    await db_session.execute(text("SELECT set_config('app.support_access_session_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.auth_session_id', '', true)"))
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user.id)},
    )
    token = await create_tenant_access_token(db_session, user, tenant_id=tenant.id)

    response = await auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["permissions"] == []
    assert body["branch_assignments"] == {}
