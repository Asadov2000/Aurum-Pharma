"""RLS isolation for the roles domain: user_assignment leaks would expose
who works for which tenant.

Tenant A's user_assignment rows must be invisible to Tenant B (and vice
versa) when reading through the app pool. System `role` rows (tenant_id
IS NULL) must remain visible to everyone, because that's the role
catalogue every tenant relies on.
"""

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


async def test_user_assignment_isolated_between_tenants(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
) -> None:
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    try:
        # ---- seed two tenants + one user-assignment each (system seller role) ----
        async with support_engine_iso.begin() as conn:
            t_rows = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('IsoRoles A', 'roles-a@aurum.tj'), "
                    "('IsoRoles B', 'roles-b@aurum.tj') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(row[0]) for row in t_rows.fetchall()]

            u_rows = await conn.execute(
                text(
                    "INSERT INTO app_user (email, full_name, home_tenant_id) VALUES "
                    "('iso-roles-a@aurum.tj', 'A worker', :a), "
                    "('iso-roles-b@aurum.tj', 'B worker', :b) "
                    "RETURNING id"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1]},
            )
            user_ids = [str(row[0]) for row in u_rows.fetchall()]

            seller_id = (
                await conn.execute(
                    text("SELECT id FROM role WHERE is_system = true AND name = 'seller'")
                )
            ).scalar_one()

            await conn.execute(
                text(
                    "INSERT INTO user_assignment (user_id, tenant_id, role_id) VALUES "
                    "(:ua, :ta, :r), (:ub, :tb, :r)"
                ),
                {
                    "ua": user_ids[0],
                    "ub": user_ids[1],
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "r": str(seller_id),
                },
            )

        # ---- Tenant A sees only its own assignment ----
        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            rows = (
                await app_conn.execute(
                    text("SELECT tenant_id FROM user_assignment " "WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            visible = {str(r[0]) for r in rows}
            assert visible == {
                tenant_ids[0]
            }, f"Tenant A should see only own assignment, saw {visible}"

        # ---- Tenant B sees only its own assignment ----
        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[1])
            rows = (
                await app_conn.execute(
                    text("SELECT tenant_id FROM user_assignment " "WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            visible = {str(r[0]) for r in rows}
            assert visible == {
                tenant_ids[1]
            }, f"Tenant B should see only own assignment, saw {visible}"

        # ---- System roles (tenant_id IS NULL) remain visible to everyone ----
        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            rows = (
                await app_conn.execute(
                    text("SELECT name FROM role WHERE is_system = true " "ORDER BY level")
                )
            ).fetchall()
            names = [r[0] for r in rows]
            assert names == ["developer", "administrator", "owner", "seller"]
    finally:
        if user_ids or tenant_ids:
            async with support_engine_iso.begin() as conn:
                if tenant_ids:
                    await conn.execute(
                        text("DELETE FROM user_assignment " "WHERE tenant_id = ANY(:ids)"),
                        {"ids": tenant_ids},
                    )
                if user_ids:
                    await conn.execute(
                        text("DELETE FROM app_user WHERE id = ANY(:ids)"),
                        {"ids": user_ids},
                    )
                if tenant_ids:
                    await conn.execute(
                        text("DELETE FROM tenant_settings " "WHERE tenant_id = ANY(:ids)"),
                        {"ids": tenant_ids},
                    )
                    await conn.execute(
                        text("DELETE FROM tenant WHERE id = ANY(:ids)"),
                        {"ids": tenant_ids},
                    )
