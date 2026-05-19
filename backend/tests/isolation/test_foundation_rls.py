"""Row-level security: a tenant must not see another tenant's data.

Strategy:
- Seed two tenants + one branch each via the *support* engine (BYPASSRLS).
  We commit the seed so it is visible to a separate connection — the test
  itself then cleans up explicitly at the end.
- Read via the *app* engine (RLS enforced), setting `app.tenant_id` GUC
  on each connection, and assert that each tenant only sees its own row.
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


async def test_tenant_a_does_not_see_branches_of_tenant_b(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
) -> None:
    tenant_ids: list[str] = []
    try:
        # ---- seed via support pool (BYPASSRLS) and commit so other
        # connections can see the rows ----
        async with support_engine_iso.begin() as conn:
            result = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('Iso A', 'iso-a@aurum.tj'), ('Iso B', 'iso-b@aurum.tj') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(row[0]) for row in result.fetchall()]
            await conn.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) VALUES " "(:a, 'A-main'), (:b, 'B-main')"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1]},
            )

        # ---- read via app pool as Tenant A ----
        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            rows = (
                await app_conn.execute(
                    text("SELECT name FROM branch WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
        names_a = sorted(r[0] for r in rows)
        assert names_a == ["A-main"], f"Tenant A should only see A-main, saw {names_a}"

        # ---- read via app pool as Tenant B ----
        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[1])
            rows = (
                await app_conn.execute(
                    text("SELECT name FROM branch WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
        names_b = sorted(r[0] for r in rows)
        assert names_b == ["B-main"], f"Tenant B should only see B-main, saw {names_b}"

        # ---- read via app pool with NO tenant context — should see nothing ----
        async with app_engine_iso.connect() as app_conn:
            rows = (
                await app_conn.execute(
                    text("SELECT name FROM branch WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
        assert rows == [], f"unset tenant should see no rows, saw {rows}"
    finally:
        # Clean up via support pool — RLS would block deletes from the app side.
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
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
