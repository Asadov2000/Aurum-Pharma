"""Fixtures for roles-domain tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select
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
)


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
    ) -> AppUser:
        u = AppUser(
            email=email or f"u-{uuid4().hex[:8]}@aurum.tj",
            full_name=full_name,
            home_tenant_id=home_tenant_id,
            status=status,
        )
        db_session.add(u)
        await db_session.flush()
        await db_session.refresh(u)
        return u

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

    async def _make(*, tenant_id, template_name: str, level: int, name: str | None = None) -> Role:
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
        )
        db_session.add(role)
        await db_session.flush()
        await db_session.refresh(role)
        for code in codes:
            db_session.add(RolePermission(role_id=role.id, permission_code=code))
        await db_session.flush()
        return role

    return _make
