"""Pending membership, preassignment, and assignment isolation tests."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.security import generate_code_salt, hash_code
from app.core.time import utc_now
from app.domains.auth.models import AppUser, EmailCode
from app.domains.auth.repository import AuthRepository
from app.domains.auth.service import AuthService
from app.domains.foundation.models import Branch
from app.domains.roles.models import (
    Role,
    RolePermission,
    TenantMembership,
    UserAssignment,
)
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService


def _tenantwide_scopes(
    permissions: set[str],
) -> dict[str, frozenset[UUID] | None]:
    return {permission: None for permission in permissions}


async def _owner_context(
    db_session: AsyncSession,
    *,
    tenant_id,
    make_owner,
):  # type: ignore[no-untyped-def]
    owner, _membership, _ownership, owner_role = await make_owner(tenant_id=tenant_id)
    repo = RolesRepository(db_session)
    permissions = set(await repo.get_role_permissions(owner_role.id))
    return owner, owner_role, permissions, RolesService(repo)


async def _custom_cashier_role(
    service: RolesService,
    *,
    owner,
    tenant_id,
    owner_permissions: set[str],
):  # type: ignore[no-untyped-def]
    role, _codes = await service.create_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant_id,
        name=f"Кассир {str(owner.id)[:8]}",
        description=None,
        permission_codes=["catalog.view", "pos.sell"],
    )
    return role


async def _seed_code(
    db_session: AsyncSession,
    *,
    email: str,
    code: str = "123456",
) -> None:
    salt = generate_code_salt()
    db_session.add(
        EmailCode(
            email_lower=email.lower(),
            code_hash=hash_code(code, salt),
            code_salt=salt,
            purpose="login",
            ip_address="127.0.0.1",
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )
    await db_session.flush()


async def test_pending_membership_can_be_preassigned_but_has_no_permissions_until_acceptance(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    other_tenant = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant.id,
        owner_permissions=owner_permissions,
    )
    account, membership = await service.create_tenant_account(
        tenant_id=tenant.id,
        email="pending-assignee@aurum.tj",
        full_name="Pending Assignee",
        actor_id=owner.id,
    )
    other_membership = TenantMembership(
        tenant_id=other_tenant.id,
        user_id=account.id,
        full_name="Other pending membership",
        status="pending",
    )
    db_session.add(other_membership)
    await db_session.flush()
    assignment = await service.assign_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_permission_scopes=_tenantwide_scopes(owner_permissions),
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        target_user_id=account.id,
        role_id=role.id,
        branch_id=None,
        password_required=False,
    )

    assert membership.status == "pending"
    assert assignment.membership_id == membership.id
    assert await service.get_effective_permissions(account.id, tenant.id) == set()

    await _seed_code(db_session, email=account.email)
    await AuthService(AuthRepository(db_session)).verify_login_code(
        email=account.email,
        code="123456",
        password=None,
        ip_address="127.0.0.1",
    )
    await db_session.refresh(account)
    await db_session.refresh(membership)
    await db_session.refresh(other_membership)

    assert account.status == "active"
    assert membership.status == "active"
    assert other_membership.status == "pending"
    assert await service.get_effective_permissions(account.id, tenant.id) == {
        "catalog.view",
        "pos.sell",
    }
    assert await service.get_effective_permissions(account.id, other_tenant.id) == set()


async def test_failed_login_does_not_activate_pending_membership(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant.id,
        owner_permissions=owner_permissions,
    )
    account, membership = await service.create_tenant_account(
        tenant_id=tenant.id,
        email="pending-failed-login@aurum.tj",
        full_name="Pending",
    )
    await service.assign_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_permission_scopes=_tenantwide_scopes(owner_permissions),
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        target_user_id=account.id,
        role_id=role.id,
        branch_id=None,
        password_required=False,
    )
    await _seed_code(db_session, email=account.email)

    from app.core.errors import AuthenticationError

    with pytest.raises(AuthenticationError):
        await AuthService(AuthRepository(db_session)).verify_login_code(
            email=account.email,
            code="000000",
            password=None,
            ip_address="127.0.0.1",
        )

    await db_session.refresh(membership)
    assert membership.status == "pending"
    assert await service.get_effective_permissions(account.id, tenant.id) == set()


async def test_owner_invite_route_cannot_create_global_account(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant.id,
        owner_permissions=owner_permissions,
    )

    with pytest.raises(PermissionDeniedError, match="cannot create accounts"):
        await service.invite_user(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_permission_scopes=_tenantwide_scopes(owner_permissions),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            email="not-created@aurum.tj",
            full_name="Not Created",
            role_id=role.id,
            branch_id=None,
            password_required=False,
        )

    assert (
        await db_session.execute(
            select(AppUser).where(AppUser.email_lower == "not-created@aurum.tj")
        )
    ).scalar_one_or_none() is None


async def test_assignment_rejects_foreign_membership(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant_a.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant_a.id,
        owner_permissions=owner_permissions,
    )
    foreign_account, _membership = await service.create_tenant_account(
        tenant_id=tenant_b.id,
        email="foreign-member@aurum.tj",
        full_name="Foreign",
    )

    with pytest.raises(NotFoundError, match="membership"):
        await service.assign_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_permission_scopes=_tenantwide_scopes(owner_permissions),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant_a.id,
            target_user_id=foreign_account.id,
            role_id=role.id,
            branch_id=None,
            password_required=False,
        )


@pytest.mark.parametrize("status", ["suspended", "offboarded"])
async def test_assignment_rejects_inactive_membership(
    status: str,
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant.id,
        owner_permissions=owner_permissions,
    )
    account, membership = await service.create_tenant_account(
        tenant_id=tenant.id,
        email=f"{status}@aurum.tj",
        full_name=status,
    )
    membership.status = status
    await db_session.flush()

    with pytest.raises(BusinessRuleError, match="pending or active"):
        await service.assign_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_permission_scopes=_tenantwide_scopes(owner_permissions),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            target_user_id=account.id,
            role_id=role.id,
            branch_id=None,
            password_required=False,
        )


async def test_protected_owner_role_cannot_be_assigned_through_role_api(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    account, _membership = await service.create_tenant_account(
        tenant_id=tenant.id,
        email="owner-role-target@aurum.tj",
        full_name="Target",
    )

    with pytest.raises(PermissionDeniedError, match="Protected"):
        await service.assign_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_permission_scopes=_tenantwide_scopes(owner_permissions),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            target_user_id=account.id,
            role_id=owner_role.id,
            branch_id=None,
            password_required=False,
        )


async def test_owner_cannot_assign_role_to_self(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant.id,
        owner_permissions=owner_permissions,
    )

    with pytest.raises(PermissionDeniedError, match="yourself"):
        await service.assign_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_permission_scopes=_tenantwide_scopes(owner_permissions),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            target_user_id=owner.id,
            role_id=role.id,
            branch_id=None,
            password_required=False,
        )


async def test_support_cannot_assign_regular_role_to_active_owner(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
    make_user,
) -> None:
    tenant = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant.id,
        owner_permissions=owner_permissions,
    )
    target_owner = await make_user(
        email="protected-assignment-owner@aurum.tj",
        home_tenant_id=tenant.id,
        is_owner=True,
    )
    developer = await make_user(is_developer=True)

    with pytest.raises(PermissionDeniedError, match="protected ownership workflow"):
        await service.assign_role(
            actor_id=developer.id,
            actor_permissions=set(),
            actor_permission_scopes={},
            actor_is_developer=True,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            target_user_id=target_owner.id,
            role_id=role.id,
            branch_id=None,
            password_required=False,
        )

    with pytest.raises(DBAPIError, match="protected ownership workflow"):
        async with db_session.begin_nested():
            await service.repo.insert_assignment(
                user_id=target_owner.id,
                tenant_id=tenant.id,
                branch_id=None,
                role_id=role.id,
                password_required=False,
            )

    assignments = await service.repo.list_assignments_for_user(
        target_owner.id,
        tenant_id=tenant.id,
    )
    assert assignments == []


async def test_support_cannot_revoke_existing_assignment_from_active_owner(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
    make_user,
) -> None:
    tenant = await make_tenant()
    _owner, owner_role, _owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    target = await make_user(
        email="protected-revocation-owner@aurum.tj",
        home_tenant_id=tenant.id,
        is_owner=True,
    )
    assignment = await service.repo.insert_assignment(
        user_id=target.id,
        tenant_id=tenant.id,
        branch_id=None,
        role_id=owner_role.id,
        password_required=False,
    )
    developer = await make_user(is_developer=True)

    with pytest.raises(PermissionDeniedError, match="protected ownership workflow"):
        await service.revoke_assignment(
            actor_id=developer.id,
            actor_permissions=set(),
            actor_permission_scopes={},
            actor_is_developer=True,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            target_user_id=target.id,
            assignment_id=assignment.id,
        )

    with pytest.raises(DBAPIError, match="protected ownership workflow"):
        async with db_session.begin_nested():
            await service.repo.deactivate_assignment(
                assignment.id,
                tenant_id=tenant.id,
            )

    await db_session.refresh(assignment)
    assert assignment.is_active is True


async def test_database_rejects_ownership_activation_with_regular_assignments(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
    make_user,
) -> None:
    tenant = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant.id,
        owner_permissions=owner_permissions,
    )
    target = await make_user(
        email="ownership-with-regular-role@aurum.tj",
        home_tenant_id=tenant.id,
    )
    await service.assign_role(
        actor_id=owner.id,
        actor_permissions=owner_permissions,
        actor_permission_scopes=_tenantwide_scopes(owner_permissions),
        actor_is_developer=False,
        actor_is_administrator=False,
        tenant_id=tenant.id,
        target_user_id=target.id,
        role_id=role.id,
        branch_id=None,
        password_required=False,
    )
    membership = await service.repo.get_membership_for_user(
        tenant_id=tenant.id,
        user_id=target.id,
    )
    assert membership is not None

    with pytest.raises(DBAPIError, match="protected owner assignments only"):
        async with db_session.begin_nested():
            await service.repo.insert_ownership(
                tenant_id=tenant.id,
                membership_id=membership.id,
                is_active=True,
            )

    assert (
        await service.repo.has_active_ownership(
            tenant_id=tenant.id,
            user_id=target.id,
        )
        is False
    )


async def test_membership_lookup_never_links_same_email_from_another_tenant(
    db_session: AsyncSession,
    make_tenant,
    make_owner,
) -> None:
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()
    owner, _owner_role, owner_permissions, service = await _owner_context(
        db_session,
        tenant_id=tenant_a.id,
        make_owner=make_owner,
    )
    role = await _custom_cashier_role(
        service,
        owner=owner,
        tenant_id=tenant_a.id,
        owner_permissions=owner_permissions,
    )
    await service.create_tenant_account(
        tenant_id=tenant_b.id,
        email="same-email-foreign@aurum.tj",
        full_name="Foreign",
    )

    with pytest.raises(PermissionDeniedError, match="cannot create accounts"):
        await service.invite_user(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_permission_scopes=_tenantwide_scopes(owner_permissions),
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant_a.id,
            email="same-email-foreign@aurum.tj",
            full_name="Foreign",
            role_id=role.id,
            branch_id=None,
            password_required=False,
        )

    memberships = list(
        (
            await db_session.execute(
                select(TenantMembership).where(
                    TenantMembership.user_id.in_(
                        select(AppUser.id).where(
                            AppUser.email_lower == "same-email-foreign@aurum.tj"
                        )
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert [membership.tenant_id for membership in memberships] == [tenant_b.id]


async def test_role_capability_cannot_be_delegated_outside_its_branch_scope(
    db_session: AsyncSession,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    actor = await make_user(
        email="scoped-owner@aurum.tj",
        home_tenant_id=tenant.id,
        is_owner=True,
    )
    target = await make_user(
        email="scoped-target@aurum.tj",
        home_tenant_id=tenant.id,
    )
    branch_a = Branch(tenant_id=tenant.id, name="Scope A")
    branch_b = Branch(tenant_id=tenant.id, name="Scope B")
    delegated_role = Role(
        tenant_id=tenant.id,
        name="Scoped cashier",
        level=4,
        is_system=False,
    )
    db_session.add_all([branch_a, branch_b, delegated_role])
    await db_session.flush()
    await db_session.refresh(branch_a)
    await db_session.refresh(branch_b)
    await db_session.refresh(delegated_role)
    db_session.add(RolePermission(role_id=delegated_role.id, permission_code="pos.sell"))
    await db_session.flush()

    with pytest.raises(PermissionDeniedError, match="target assignment scope"):
        await RolesService(RolesRepository(db_session)).assign_role(
            actor_id=actor.id,
            actor_permissions={"roles.assign", "pos.sell"},
            actor_permission_scopes={
                "roles.assign": frozenset({branch_b.id}),
                "pos.sell": frozenset({branch_a.id}),
            },
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant.id,
            target_user_id=target.id,
            role_id=delegated_role.id,
            branch_id=branch_b.id,
            password_required=False,
        )

    assignment = (
        await db_session.execute(
            select(UserAssignment).where(
                UserAssignment.tenant_id == tenant.id,
                UserAssignment.user_id == target.id,
            )
        )
    ).scalar_one_or_none()
    assert assignment is None
