"""Personal profile is readable without directory permissions and never expands scope."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.domains.auth.models import AppUser
from app.domains.auth.profile_repository import ProfileRepository
from app.domains.auth.profile_service import ProfileService
from app.domains.auth.repository import AuthRepository
from app.domains.foundation.models import Branch, Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import Role, TenantMembership, UserAssignment
from app.domains.roles.repository import RolesRepository
from tests.auth_helpers import create_support_access_token, create_tenant_access_token
from tests.platform_access_helpers import create_test_platform_user
from tests.role_version_helpers import create_published_test_role, provision_test_owner


@dataclass
class AssignmentFixture:
    user: AppUser
    tenant: Tenant
    branch: Branch
    role: Role
    assignment: UserAssignment


async def _assignment(db: AsyncSession, user: AppUser | None = None) -> AssignmentFixture:
    foundation = FoundationService(FoundationRepository(db))
    suffix = uuid4().hex[:8]
    tenant = await foundation.create_tenant(
        payload={"name": f"Profile {suffix}", "contact_email": f"{suffix}@example.invalid"}
    )
    if user is None:
        user = AppUser(
            email=f"profile-user-{suffix}@example.invalid",
            full_name="Profile user",
            home_tenant_id=tenant.id,
            status="active",
        )
        db.add(user)
        await db.flush()
    branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Точка"})
    role = await create_published_test_role(
        db,
        tenant_id=tenant.id,
        name=f"Кассир {suffix}",
        permission_codes=["pos.sell"],
        level=4,
    )
    membership = TenantMembership(
        tenant_id=tenant.id, user_id=user.id, full_name=user.full_name, status="active"
    )
    db.add(membership)
    await db.flush()
    assignment = UserAssignment(
        tenant_id=tenant.id,
        user_id=user.id,
        membership_id=membership.id,
        role_id=role.id,
        branch_id=branch.id,
    )
    db.add(assignment)
    await db.flush()
    return AssignmentFixture(user, tenant, branch, role, assignment)


async def _caller(db: AsyncSession, user: AppUser, tenant_id: UUID | None) -> str:
    await db.execute(
        text(
            "SELECT set_config('app.support_session', 'false', true), "
            "set_config('app.support_access_session_id', '', true), "
            "set_config('app.auth_session_id', '', true), "
            "set_config('app.user_id', :user_id, true), "
            "set_config('app.tenant_id', :tenant_id, true)"
        ),
        {"user_id": str(user.id), "tenant_id": str(tenant_id) if tenant_id else ""},
    )
    return await create_tenant_access_token(db, user, tenant_id=tenant_id)


async def test_profile_requires_session(auth_client: AsyncClient) -> None:
    assert (await auth_client.get("/api/v1/auth/profile")).status_code == 401


async def test_profile_without_tenant_is_identity_only(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    user = await make_user()
    token = await _caller(db_session, user, None)
    response = await auth_client.get(
        "/api/v1/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "id": str(user.id),
        "full_name": user.full_name,
        "email": user.email,
        "status": "active",
        "tenant_id": None,
        "is_tenant_owner": False,
        "platform_role": None,
        "access_assignments": [],
    }


async def test_profile_names_are_own_and_current_tenant_only(
    auth_client: AsyncClient, db_session: AsyncSession, make_user
) -> None:
    first = await _assignment(db_session)
    user = first.user
    second = await _assignment(db_session, user)
    other = await make_user()
    other_role = await create_published_test_role(
        db_session,
        tenant_id=first.tenant.id,
        name="Other employee role",
        permission_codes=["pos.sell"],
    )
    other_membership = TenantMembership(
        tenant_id=first.tenant.id, user_id=other.id, full_name=other.full_name, status="active"
    )
    db_session.add(other_membership)
    await db_session.flush()
    db_session.add(
        UserAssignment(
            tenant_id=first.tenant.id,
            user_id=other.id,
            membership_id=other_membership.id,
            role_id=other_role.id,
            branch_id=None,
        )
    )
    await db_session.flush()
    token = await _caller(db_session, user, first.tenant.id)
    response = await auth_client.get(
        "/api/v1/auth/profile",
        headers={"Authorization": f"Bearer {token}"},
        params={"user_id": str(other.id), "tenant_id": str(second.tenant.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["tenant_id"] == str(first.tenant.id)
    assert response.json()["access_assignments"] == [
        {
            "role_id": str(first.role.id),
            "role_name": first.role.name,
            "scope": "branch",
            "branch": {"id": str(first.branch.id), "name": first.branch.name},
        }
    ]
    me = await auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["permissions"] == ["pos.sell"]
    assert "access_assignments" not in me.json()
    assert "access_assignments" not in decode_access_token(token)
    assert "permissions" not in decode_access_token(token)


@pytest.mark.parametrize("inactive", ["assignment", "branch"])
async def test_profile_excludes_inactive_assignments(
    auth_client: AsyncClient, db_session: AsyncSession, inactive: str
) -> None:
    fixture = await _assignment(db_session)
    user = fixture.user
    if inactive == "assignment":
        fixture.assignment.is_active = False
    else:
        fixture.branch.is_active = False
    await db_session.flush()
    token = await _caller(db_session, user, fixture.tenant.id)
    response = await auth_client.get(
        "/api/v1/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_assignments"] == []


async def test_profile_tenant_scope_and_renamed_labels_are_current(
    auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    fixture = await _assignment(db_session)
    user = fixture.user
    fixture.assignment.branch_id = None
    await db_session.flush()
    token = await _caller(db_session, user, fixture.tenant.id)
    first = await auth_client.get(
        "/api/v1/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["access_assignments"][0]["scope"] == "tenant"
    assert first.json()["access_assignments"][0]["branch"] is None
    await create_published_test_role(
        db_session,
        tenant_id=fixture.tenant.id,
        name="Publication context",
        permission_codes=["pos.sell"],
    )
    await RolesRepository(db_session).publish_role_version(
        role_id=fixture.role.id,
        expected_version=fixture.role.version,
        name="Новая роль",
        description=None,
        permission_codes=["pos.sell"],
    )
    token = await _caller(db_session, user, fixture.tenant.id)
    response = await auth_client.get(
        "/api/v1/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.json()["access_assignments"][0]["role_name"] == "Новая роль"


@pytest.mark.parametrize("kind", ["developer", "administrator"])
async def test_profile_platform_identity_does_not_invent_tenant_roles(
    auth_client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    user = await create_test_platform_user(db_session, access_kind=kind)
    token = await create_support_access_token(db_session, user)
    response = await auth_client.get(
        "/api/v1/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(user.id)
    assert response.json()["platform_role"] == kind
    assert response.json()["access_assignments"] == []
    assert response.json()["is_tenant_owner"] is False


async def test_support_profile_never_loads_tenant_assignments(
    db_session: AsyncSession,
) -> None:
    user = await create_test_platform_user(db_session, access_kind="administrator")
    token = await create_support_access_token(db_session, user)
    await db_session.execute(
        text(
            "SELECT set_config('app.user_id', :user_id, true), "
            "set_config('app.auth_session_id', :session_id, true)"
        ),
        {"user_id": str(user.id), "session_id": str(decode_access_token(token)["sid"])},
    )
    service = ProfileService(ProfileRepository(db_session), AuthRepository(db_session))
    result = await service.get_profile(
        user_id=user.id,
        tenant_id=uuid4(),
        branch_assignments={"not-a-branch": "not-a-role"},
        is_tenant_owner=True,
        is_support_access=True,
    )
    assert result.id == user.id
    assert result.access_assignments == []
    assert result.is_tenant_owner is False


async def test_profile_owner_is_separate_from_assignments(
    auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
        payload={"name": "Profile owner", "contact_email": "profile-tenant@example.invalid"}
    )
    user, _membership, _ownership, _role = await provision_test_owner(
        db_session,
        tenant_id=tenant.id,
        email="profile-owner@example.invalid",
        full_name="Owner",
    )
    token = await _caller(db_session, user, tenant.id)
    response = await auth_client.get(
        "/api/v1/auth/profile", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_tenant_owner"] is True
