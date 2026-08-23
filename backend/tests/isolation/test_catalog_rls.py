"""RLS: tenant A's catalog rows are invisible to tenant B."""

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


async def test_tenant_catalog_and_barcode_isolated(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
) -> None:
    tenant_ids: list[str] = []
    try:
        async with support_engine_iso.begin() as conn:
            t_rows = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('IsoCat A', 'iso-cat-a@aurum.tj'), "
                    "('IsoCat B', 'iso-cat-b@aurum.tj') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(row[0]) for row in t_rows.fetchall()]
            c_rows = await conn.execute(
                text(
                    "INSERT INTO tenant_catalog ("
                    "tenant_id, brand_name, image_version, image_width, image_height, "
                    "image_size_bytes, image_thumbnail_size_bytes, image_sha256, "
                    "image_uploaded_at, image_uploaded_by"
                    ") VALUES "
                    "(:a, 'A-Drug', gen_random_uuid(), 100, 80, 1000, 200, :sha_a, now(), "
                    "gen_random_uuid()), "
                    "(:b, 'B-Drug', gen_random_uuid(), 100, 80, 1000, 200, :sha_b, now(), "
                    "gen_random_uuid()) RETURNING id"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1], "sha_a": "a" * 64, "sha_b": "b" * 64},
            )
            catalog_ids = [str(row[0]) for row in c_rows.fetchall()]
            await conn.execute(
                text(
                    "INSERT INTO barcode (tenant_id, catalog_id, code) VALUES "
                    "(:ta, :ca, 'A-CODE'), (:tb, :cb, 'B-CODE')"
                ),
                {
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "ca": catalog_ids[0],
                    "cb": catalog_ids[1],
                },
            )

        # Tenant A sees only its own catalog rows
        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            rows = (
                await app_conn.execute(
                    text(
                        "SELECT brand_name, image_sha256 FROM tenant_catalog "
                        "WHERE tenant_id = ANY(:ids)"
                    ),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            assert sorted(r[0] for r in rows) == ["A-Drug"]
            assert rows[0][1] == "a" * 64
            hidden_update = await app_conn.execute(
                text("UPDATE tenant_catalog SET image_sha256 = :sha WHERE tenant_id = :tenant_id"),
                {"sha": "c" * 64, "tenant_id": tenant_ids[1]},
            )
            assert hidden_update.rowcount == 0

            bc_rows = (
                await app_conn.execute(
                    text("SELECT code FROM barcode WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            assert sorted(r[0] for r in bc_rows) == ["A-CODE"]

        # Tenant B mirrored
        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[1])
            rows = (
                await app_conn.execute(
                    text("SELECT brand_name FROM tenant_catalog " "WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            assert sorted(r[0] for r in rows) == ["B-Drug"]
    finally:
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
                await conn.execute(
                    text("DELETE FROM barcode WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant_catalog WHERE tenant_id = ANY(:ids)"),
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
