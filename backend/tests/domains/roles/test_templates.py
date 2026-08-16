"""Permission and template catalogues are filtered by delegation metadata."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.deps import _seed_request_db_context, get_db
from app.core.security import create_access_token, decode_access_token
from app.domains.roles.models import Permission, RolePermission
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from app.domains.support_access.repository import SupportAccessRepository
from app.domains.support_access.service import SupportAccessService
from app.main import app
from tests.auth_helpers import create_support_access_token

PROTECTED_GOVERNANCE_CODES = {
    "roles.assign",
    "roles.create",
    "roles.update",
    "users.block",
    "users.delete",
    "users.invite",
    "users.update",
}


async def test_permission_scope_metadata_uses_explicit_capability_mapping(
    db_session: AsyncSession,
) -> None:
    expected = {
        "audit.view.global": "PLATFORM",
        "audit.view.own": "OWN",
        "sales.view.own": "OWN",
        "pos.sell": "BRANCH_SET",
        "roles.assign": "TENANT_ALL",
        "incoming.create": "BRANCH_SET",
        "reports.view": "BRANCH_SET",
        "billing.overview.view": "TENANT_ALL",
        "billing.invoice.view": "TENANT_ALL",
        "catalog.view": "TENANT_ALL",
        "suppliers.view": "TENANT_ALL",
        "settings.update": "TENANT_ALL",
    }

    for code, scope_type in expected.items():
        permission = await db_session.get(Permission, code)
        assert permission is not None
        assert permission.scope_type == scope_type


async def test_owner_templates_are_intersected_with_owner_catalog(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
    repo = RolesRepository(db_session)
    service = RolesService(repo)
    owner_permissions = set(await repo.get_role_permissions(owner_role.id))
    catalog = await service.list_permissions(
        actor_id=owner.id,
        tenant_id=tenant.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
    )
    allowed = {permission.code for permission in catalog}
    templates = await service.list_templates_with_permissions(
        actor_id=owner.id,
        tenant_id=tenant.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
    )

    assert templates
    for _template, codes in templates:
        assert set(codes) <= allowed
    assert "audit.view.global" not in allowed
    assert "tenant.export.full" not in allowed
    assert allowed.isdisjoint(PROTECTED_GOVERNANCE_CODES)


async def test_support_catalogues_exclude_platform_and_protected_grants(
    db_session: AsyncSession,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    developer = await make_user(home_tenant_id=tenant.id)
    administrator = await make_user(home_tenant_id=tenant.id)
    service = RolesService(RolesRepository(db_session))

    developer_codes = {
        permission.code
        for permission in await service.list_permissions(
            actor_id=developer.id,
            tenant_id=tenant.id,
            actor_permissions=set(),
            actor_is_developer=True,
            actor_is_administrator=False,
        )
    }
    administrator_codes = {
        permission.code
        for permission in await service.list_permissions(
            actor_id=administrator.id,
            tenant_id=tenant.id,
            actor_permissions=set(),
            actor_is_developer=False,
            actor_is_administrator=True,
        )
    }

    assert "tenant.export.full" in developer_codes
    assert "tenant.export.full" not in administrator_codes
    assert "audit.view.global" not in developer_codes
    assert "audit.view.global" not in administrator_codes
    assert developer_codes.isdisjoint(PROTECTED_GOVERNANCE_CODES)
    assert administrator_codes.isdisjoint(PROTECTED_GOVERNANCE_CODES)


async def test_administrator_has_explicit_catalog_access_but_no_role_write_bypass(
    db_session: AsyncSession,
    client: AsyncClient,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    administrator = await make_user(
        email="catalog-admin@aurum.tj",
        is_administrator=True,
    )
    token = await create_support_access_token(db_session, administrator)
    actor_session_id = UUID(str(decode_access_token(token)["sid"]))
    support_service = SupportAccessService(SupportAccessRepository(db_session))
    support_session = await support_service.start_session(
        actor_user_id=administrator.id,
        actor_session_id=actor_session_id,
        actor_is_developer=False,
        actor_is_administrator=True,
        tenant_id=tenant.id,
        reason="Read the tenant user directory without role access",
        duration_minutes=10,
        requested_capabilities=["users.view"],
    )

    async def _override(request: Request) -> AsyncIterator[AsyncSession]:
        request.state.support_access_resolved = True
        request.state.tenant_id = tenant.id
        request.state.is_support_session = True
        request.state.support_access_capabilities = support_session.capabilities
        request.state.support_access_reason = support_session.reason
        request.state.support_access_expires_at = support_session.expires_at
        request.state.support_access_tenant_name = support_session.tenant_name
        request.state.support_access_is_read_only = support_session.is_read_only
        await _seed_request_db_context(request, db_session)
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Aurum-Support-Session": str(support_session.id),
        }
        users_only_response = await client.get("/api/v1/permissions", headers=headers)
        assert users_only_response.status_code == 403

        support_session = await support_service.start_session(
            actor_user_id=administrator.id,
            actor_session_id=actor_session_id,
            actor_is_developer=False,
            actor_is_administrator=True,
            tenant_id=tenant.id,
            reason="Read the tenant role catalogue without write access",
            duration_minutes=10,
            requested_capabilities=["roles.assign"],
        )
        headers["X-Aurum-Support-Session"] = str(support_session.id)

        catalog_response = await client.get("/api/v1/permissions", headers=headers)
        assert catalog_response.status_code == 200
        assert "audit.view.global" not in {item["code"] for item in catalog_response.json()}

        create_response = await client.post(
            "/api/v1/roles",
            headers=headers,
            json={"name": "Admin bypass", "permissions": ["pos.sell"]},
        )
        assert create_response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_non_owner_with_roles_create_cannot_read_delegation_catalog(
    db_session: AsyncSession,
    client: AsyncClient,
    make_tenant,
    make_tenant_role,
    make_user,
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await make_tenant()
        user = await make_user(home_tenant_id=tenant.id)
        role = await make_tenant_role(
            tenant_id=tenant.id,
            template_name="Кассир",
            level=4,
        )
        # The HTTP gate will pass only if roles.create is present, but the
        # service still requires the protected ownership root.
        from app.domains.roles.models import RolePermission, UserAssignment

        db_session.add(RolePermission(role_id=role.id, permission_code="roles.create"))
        await db_session.flush()
        membership = await RolesRepository(db_session).get_membership_for_user(
            tenant_id=tenant.id,
            user_id=user.id,
        )
        assert membership is not None
        db_session.add(
            UserAssignment(
                user_id=user.id,
                tenant_id=tenant.id,
                membership_id=membership.id,
                role_id=role.id,
            )
        )
        await db_session.flush()
        token = create_access_token(
            user.id,
            tenant_id=tenant.id,
            is_developer=False,
            is_administrator=False,
        )

        response = await client.get(
            "/api/v1/permissions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_role_catalog_does_not_disclose_protected_owner_role(
    db_session: AsyncSession,
    client: AsyncClient,
    make_tenant,
    make_owner,
    make_tenant_role,
) -> None:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        tenant = await make_tenant()
        owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant.id)
        staff_role = await make_tenant_role(
            tenant_id=tenant.id,
            template_name="Кассир",
            level=4,
            name="Visible cashier",
        )
        db_session.add(
            RolePermission(
                role_id=staff_role.id,
                permission_code="audit.view.global",
            )
        )
        await db_session.flush()
        token = create_access_token(
            owner.id,
            tenant_id=tenant.id,
            is_developer=False,
            is_administrator=False,
        )

        response = await client.get(
            "/api/v1/roles",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert {item["id"] for item in body} == {str(staff_role.id)}
        assert body[0]["has_hidden_permissions"] is True
        assert "audit.view.global" not in body[0]["permissions"]
        serialized = str(body)
        assert str(owner_role.id) not in serialized
        assert "tenant_owner" not in serialized
        assert "audit.view.global" not in serialized
    finally:
        app.dependency_overrides.pop(get_db, None)
