"""Fixtures for roles-domain tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import (
    Role,
    RolePermission,
    RoleTemplate,
    RoleTemplatePermission,
    TenantMembership,
    TenantOwnership,
)
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from tests.platform_access_helpers import create_test_platform_user
from tests.role_version_helpers import set_test_recent_confirmation


@pytest_asyncio.fixture
async def make_tenant(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """Insert a tenant with default settings (no extra HTTP fixtures needed)."""
    service = FoundationService(FoundationRepository(db_session))

    async def _make(name: str | None = None) -> Tenant:
        nick = uuid4().hex[:8]
        return await service.create_tenant(
            payload={
                "name": name or f"Tenant {nick}",
                "contact_email": f"t-{nick}@aurum.tj",
            }
        )

    return _make


@pytest_asyncio.fixture
async def make_user(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    async def _make(
        *,
        email: str | None = None,
        full_name: str = "Test User",
        home_tenant_id=None,
        status: str = "active",
        membership_status: str = "active",
        is_owner: bool = False,
        is_developer: bool = False,
        is_administrator: bool = False,
    ) -> AppUser:
        if is_developer or is_administrator:
            if is_developer and is_administrator:
                raise ValueError("A test platform account must have one access kind")
            if home_tenant_id is not None:
                raise ValueError("A platform account cannot have a tenant")
            return await create_test_platform_user(
                db_session,
                access_kind="developer" if is_developer else "administrator",
                email=email,
                full_name=full_name,
                status=status,
            )
        u = AppUser(
            email=email or f"u-{uuid4().hex[:8]}@aurum.tj",
            full_name=full_name,
            home_tenant_id=home_tenant_id,
            status=status,
        )
        db_session.add(u)
        await db_session.flush()
        await db_session.refresh(u)
        if home_tenant_id is not None:
            membership = TenantMembership(
                tenant_id=home_tenant_id,
                user_id=u.id,
                full_name=full_name,
                status=membership_status,
            )
            db_session.add(membership)
            await db_session.flush()
            await db_session.refresh(membership)
            if is_owner:
                db_session.add(
                    TenantOwnership(
                        tenant_id=home_tenant_id,
                        membership_id=membership.id,
                    )
                )
                await db_session.flush()
        return u

    return _make


@pytest_asyncio.fixture
async def make_owner(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    actor: AppUser | None = None

    async def _make(*, tenant_id, email: str | None = None, full_name: str = "Owner"):
        nonlocal actor
        if actor is None:
            actor = await create_test_platform_user(
                db_session,
                access_kind="developer",
                email=f"owner-actor-{uuid4().hex[:8]}@aurum.tj",
                full_name="Owner provisioning actor",
            )
        await db_session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(actor.id)},
        )
        await set_test_recent_confirmation(db_session, user_id=actor.id)
        provisioned = await RolesService(RolesRepository(db_session)).provision_owner(
            tenant_id=tenant_id,
            email=email or f"owner-{uuid4().hex[:8]}@aurum.tj",
            full_name=full_name,
            actor_id=actor.id,
        )
        owner, _membership, _ownership, _role = provisioned
        await set_test_recent_confirmation(db_session, user_id=owner.id)
        await db_session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(owner.id)},
        )
        await db_session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        return provisioned

    return _make


@pytest_asyncio.fixture
async def system_roles(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, Role]]:
    """Map name -> Role for the seeded system roles (developer, administrator;
    owner/seller were demoted to tenant roles in migration 0020)."""
    stmt = select(Role).where(Role.is_system.is_(True))
    result = await db_session.execute(stmt)
    by_name = {role.name: role for role in result.scalars().all()}
    yield by_name


@pytest_asyncio.fixture
async def make_tenant_role(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    """Create a tenant role from a template's permission set.

    owner/seller are no longer system roles, so tests that need an
    owner-/seller-shaped role build one per tenant from the «Владелец» /
    «Кассир» templates seeded in migration 0019.
    """

    support_actor = await create_test_platform_user(
        db_session,
        access_kind="developer",
        email=f"role-actor-{uuid4().hex[:8]}@aurum.tj",
        full_name="Role fixture actor",
    )

    async def _make(*, tenant_id, template_name: str, level: int, name: str | None = None) -> Role:
        await set_test_recent_confirmation(db_session, user_id=support_actor.id)
        await db_session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(support_actor.id)},
        )
        await db_session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        tpl = (
            await db_session.execute(select(RoleTemplate).where(RoleTemplate.name == template_name))
        ).scalar_one()
        codes = list(
            (
                await db_session.execute(
                    select(RoleTemplatePermission.permission_code).where(
                        RoleTemplatePermission.template_id == tpl.id
                    )
                )
            )
            .scalars()
            .all()
        )
        role = Role(
            tenant_id=tenant_id,
            name=name or template_name,
            level=level,
            is_system=False,
            is_protected=template_name == "Владелец",
            protected_kind="tenant_owner" if template_name == "Владелец" else None,
        )
        db_session.add(role)
        await db_session.flush()
        await db_session.refresh(role)
        for code in codes:
            db_session.add(RolePermission(role_id=role.id, permission_code=code))
        await db_session.flush()
        await RolesRepository(db_session).initialize_role_version(role.id)
        return role

    return _make
