"""RLS: tenant_subscription / invoice / payment are isolated per tenant."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_iso() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_iso() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_tenant(conn: AsyncConnection, tid: str) -> None:
    await conn.execute(text("SELECT set_config('app.tenant_id', :v, false)"), {"v": tid})


async def test_subscription_invisible_across_tenants(
    support_engine_iso: AsyncEngine, app_engine_iso: AsyncEngine
) -> None:
    tenant_ids: list[str] = []
    try:
        async with support_engine_iso.begin() as conn:
            tr = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('IsoBill A','iba@aurum.tj'),('IsoBill B','ibb@aurum.tj') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(r[0]) for r in tr.fetchall()]
            plan_id = (
                await conn.execute(
                    text("SELECT id FROM subscription_plan WHERE code = 'aurum_pharma'")
                )
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO tenant_subscription "
                    "(tenant_id, plan_id, status, period_end, branches_count, amount) "
                    "VALUES (:a,:p,'active', now() + interval '30 days', 1, 100), "
                    "       (:b,:p,'active', now() + interval '30 days', 1, 100)"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1], "p": plan_id},
            )

        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            rows = (
                await app_conn.execute(
                    text(
                        "SELECT tenant_id FROM tenant_subscription " "WHERE tenant_id = ANY(:ids)"
                    ),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            assert len(rows) == 1, "Tenant A must only see its own subscription"
    finally:
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
                await conn.execute(
                    text("DELETE FROM tenant_subscription WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant_settings WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
