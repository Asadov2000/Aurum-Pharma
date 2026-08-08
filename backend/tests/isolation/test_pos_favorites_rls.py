"""RLS isolates personal POS favorites by tenant and current app user."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_favorites() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_favorites() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_pos_favorites_rls_enforces_tenant_and_user_ownership(
    support_engine_favorites: AsyncEngine,
    app_engine_favorites: AsyncEngine,
) -> None:
    suffix = uuid4().hex[:8]
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    try:
        async with support_engine_favorites.begin() as conn:
            tenants = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email, status) VALUES "
                    "(:name_a, :email_a, 'active'), (:name_b, :email_b, 'active') "
                    "RETURNING id"
                ),
                {
                    "name_a": f"Favorite RLS A {suffix}",
                    "email_a": f"favorite-rls-a-{suffix}@aurum.tj",
                    "name_b": f"Favorite RLS B {suffix}",
                    "email_b": f"favorite-rls-b-{suffix}@aurum.tj",
                },
            )
            tenant_ids = [str(row[0]) for row in tenants]
            users = await conn.execute(
                text(
                    "INSERT INTO app_user (email, full_name, home_tenant_id, status) VALUES "
                    "(:email_a1, 'A1', :tenant_a, 'active'), "
                    "(:email_a2, 'A2', :tenant_a, 'active'), "
                    "(:email_b, 'B', :tenant_b, 'active') RETURNING id"
                ),
                {
                    "email_a1": f"favorite-user-a1-{suffix}@aurum.tj",
                    "email_a2": f"favorite-user-a2-{suffix}@aurum.tj",
                    "email_b": f"favorite-user-b-{suffix}@aurum.tj",
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                },
            )
            user_ids = [str(row[0]) for row in users]
            catalog = await conn.execute(
                text(
                    "INSERT INTO tenant_catalog (tenant_id, brand_name) VALUES "
                    "(:tenant_a, :name_a1), (:tenant_a, :name_a2), "
                    "(:tenant_b, :name_b) RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "name_a1": f"A1 {suffix}",
                    "name_a2": f"A2 {suffix}",
                    "name_b": f"B {suffix}",
                },
            )
            catalog_ids = [str(row[0]) for row in catalog]
            await conn.execute(
                text(
                    "INSERT INTO pos_favorite (tenant_id, user_id, catalog_id) VALUES "
                    "(:tenant_a, :user_a1, :catalog_a1), "
                    "(:tenant_a, :user_a2, :catalog_a2), "
                    "(:tenant_b, :user_b, :catalog_b)"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "user_a1": user_ids[0],
                    "user_a2": user_ids[1],
                    "user_b": user_ids[2],
                    "catalog_a1": catalog_ids[0],
                    "catalog_a2": catalog_ids[1],
                    "catalog_b": catalog_ids[2],
                },
            )

        async with app_engine_favorites.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :value, false)"),
                {"value": tenant_ids[0]},
            )
            await conn.execute(
                text("SELECT set_config('app.user_id', :value, false)"),
                {"value": user_ids[0]},
            )
            rows = (await conn.execute(text("SELECT user_id FROM pos_favorite"))).scalars().all()
            assert [str(user_id) for user_id in rows] == [user_ids[0]]

            await conn.execute(
                text("SELECT set_config('app.user_id', :value, false)"),
                {"value": user_ids[1]},
            )
            rows = (await conn.execute(text("SELECT user_id FROM pos_favorite"))).scalars().all()
            assert [str(user_id) for user_id in rows] == [user_ids[1]]

            await conn.execute(
                text("SELECT set_config('app.tenant_id', :value, false)"),
                {"value": tenant_ids[1]},
            )
            rows = (await conn.execute(text("SELECT user_id FROM pos_favorite"))).scalars().all()
            assert rows == []
    finally:
        if tenant_ids:
            async with support_engine_favorites.begin() as conn:
                await conn.execute(
                    text("DELETE FROM pos_favorite WHERE tenant_id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant_catalog WHERE tenant_id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                if user_ids:
                    await conn.execute(
                        text("DELETE FROM app_user WHERE id = ANY(:user_ids)"),
                        {"user_ids": user_ids},
                    )
