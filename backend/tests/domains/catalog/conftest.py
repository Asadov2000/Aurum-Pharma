"""Fixtures for catalog tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.domains.auth.models import AppUser
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService


@pytest_asyncio.fixture
async def make_tenant(db_session: AsyncSession):  # type: ignore[no-untyped-def]
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
async def committed_catalog_import(
    db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], UUID, UUID]]:
    """One pending job visible to independent sessions, only on disposable DBs."""
    for engine in (db_engine, maintenance_engine):
        if not (engine.url.database or "").endswith("_test"):
            raise RuntimeError("Import lifecycle tests require a disposable '_test' database")
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory.begin() as session:
        tenant = await FoundationService(FoundationRepository(session)).create_tenant(
            payload={
                "name": "Import lifecycle",
                "contact_email": f"import-{uuid4().hex}@example.invalid",
            }
        )
        user = AppUser(
            email=f"import-user-{uuid4().hex}@example.invalid",
            full_name="Import lifecycle",
            home_tenant_id=tenant.id,
            status="active",
        )
        session.add(user)
        await session.flush()
        job = await CatalogService(CatalogRepository(session)).create_import_job(
            tenant_id=tenant.id,
            user_id=user.id,
            source_filename="lifecycle.csv",
            source_path=f"aurum/{tenant.id}/imports/{uuid4()}.csv",
        )
        tenant_id, user_id, job_id = tenant.id, user.id, job.id
    try:
        yield factory, tenant_id, job_id
    finally:
        # Only this fixture's generated tenant is eligible for cleanup. Remove
        # catalog results too, since worker tests may have committed an import.
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.barcode WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.tenant_catalog WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.catalog_import_job WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        async with db_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": user_id},
            )


@pytest_asyncio.fixture
async def make_user(db_session: AsyncSession):  # type: ignore[no-untyped-def]
    async def _make(
        *,
        email: str | None = None,
        home_tenant_id=None,
    ) -> AppUser:
        u = AppUser(
            email=email or f"u-{uuid4().hex[:8]}@aurum.tj",
            full_name="Test User",
            home_tenant_id=home_tenant_id,
            status="active",
        )
        db_session.add(u)
        await db_session.flush()
        await db_session.refresh(u)
        return u

    return _make
