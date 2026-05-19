"""RLS isolation across batch / batch_movement / write_off."""

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


async def _set_tenant(conn: AsyncConnection, tenant_id: str) -> None:
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :v, false)"),
        {"v": tenant_id},
    )


async def test_batch_invisible_across_tenants(
    support_engine_iso: AsyncEngine, app_engine_iso: AsyncEngine
) -> None:
    tenant_ids: list[str] = []
    try:
        async with support_engine_iso.begin() as conn:
            tres = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('IsoBatch A', 'iba@aurum.tj'), ('IsoBatch B', 'ibb@aurum.tj') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(row[0]) for row in tres.fetchall()]
            bres = await conn.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) VALUES "
                    "(:a, 'A-Main'), (:b, 'B-Main') RETURNING id"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1]},
            )
            branch_ids = [str(row[0]) for row in bres.fetchall()]
            cres = await conn.execute(
                text(
                    "INSERT INTO tenant_catalog (tenant_id, brand_name) VALUES "
                    "(:a, 'A-Drug'), (:b, 'B-Drug') RETURNING id"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1]},
            )
            catalog_ids = [str(row[0]) for row in cres.fetchall()]
            await conn.execute(
                text(
                    "INSERT INTO batch (tenant_id, branch_id, catalog_id, "
                    "expires_at, purchase_price, sale_price, qty_initial, qty_remaining) "
                    "VALUES "
                    "(:ta, :ba, :ca, CURRENT_DATE + 30, 5, 10, 5, 5), "
                    "(:tb, :bb, :cb, CURRENT_DATE + 30, 5, 10, 5, 5)"
                ),
                {
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "ba": branch_ids[0],
                    "bb": branch_ids[1],
                    "ca": catalog_ids[0],
                    "cb": catalog_ids[1],
                },
            )

        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            rows = (
                await app_conn.execute(
                    text(
                        "SELECT b.id FROM batch b "
                        "JOIN tenant_catalog tc ON tc.id = b.catalog_id "
                        "WHERE tc.brand_name IN ('A-Drug','B-Drug')"
                    )
                )
            ).fetchall()
            assert len(rows) == 1, f"Tenant A should see one batch, saw {len(rows)}"
    finally:
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
                await conn.execute(
                    text("DELETE FROM batch WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant_catalog WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM branch WHERE tenant_id = ANY(:ids)"),
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
