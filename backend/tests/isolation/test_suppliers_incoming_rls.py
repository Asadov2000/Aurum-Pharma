"""RLS: supplier / incoming_document / supplier_return all isolated."""

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


async def test_supplier_invisible_across_tenants(
    support_engine_iso: AsyncEngine, app_engine_iso: AsyncEngine
) -> None:
    tenant_ids: list[str] = []
    try:
        async with support_engine_iso.begin() as conn:
            tr = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('IsoSup A', 'isua@aurum.tj'), ('IsoSup B', 'isub@aurum.tj') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(r[0]) for r in tr.fetchall()]
            await conn.execute(
                text(
                    "INSERT INTO supplier (tenant_id, name) VALUES "
                    "(:a, 'A-Supplier'), (:b, 'B-Supplier')"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1]},
            )

        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            names = (
                await app_conn.execute(
                    text("SELECT name FROM supplier WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            assert sorted(n[0] for n in names) == ["A-Supplier"]
    finally:
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
                await conn.execute(
                    text("DELETE FROM supplier WHERE tenant_id = ANY(:ids)"),
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
