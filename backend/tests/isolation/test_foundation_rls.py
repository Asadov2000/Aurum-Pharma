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

POLICIES_USING_SUPPORT_FLAG_SQL = """
SELECT count(*)
FROM pg_policies
WHERE schemaname = 'public'
  AND (
    COALESCE(qual, '') LIKE '%is_support_session%'
    OR COALESCE(with_check, '') LIKE '%is_support_session%'
  )
"""


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
        text("SELECT set_config('app.tenant_id', :v, true)"),
        {"v": tenant_id},
    )


async def _set_support_flag(conn: AsyncConnection) -> None:
    await conn.execute(
        text("SELECT set_config('app.support_session', 'true', true)"),
    )


async def _visible_register_names(
    engine: AsyncEngine,
    tenant_ids: list[str],
    tenant_id: str | None,
) -> list[str]:
    async with engine.begin() as conn:
        if tenant_id is not None:
            await _set_tenant(conn, tenant_id)
        rows = (
            await conn.execute(
                text("SELECT name FROM register WHERE tenant_id = ANY(:ids)"),
                {"ids": tenant_ids},
            )
        ).fetchall()
    return sorted(str(row[0]) for row in rows)


async def test_tenant_a_does_not_see_branches_of_tenant_b(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    maintenance_engine: AsyncEngine,
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
        async with app_engine_iso.begin() as app_conn:
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
        async with app_engine_iso.begin() as app_conn:
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
        async with app_engine_iso.begin() as app_conn:
            rows = (
                await app_conn.execute(
                    text("SELECT name FROM branch WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
        assert rows == [], f"unset tenant should see no rows, saw {rows}"

        # A custom GUC is caller-controlled. Setting it from aurum_app must
        # never activate the support bypass or expose Tenant B.
        async with app_engine_iso.begin() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            await _set_support_flag(app_conn)
            context = (
                await app_conn.execute(
                    text("SELECT session_user AS db_role, is_support_session() AS is_support")
                )
            ).one()
            rows = (
                await app_conn.execute(
                    text("SELECT name FROM branch WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
        names_with_forged_support = sorted(row[0] for row in rows)
        assert context.db_role == "aurum_app"
        assert context.is_support is False
        assert names_with_forged_support == ["A-main"]

        # The support login remains identifiable and uses its PostgreSQL
        # BYPASSRLS attribute rather than a caller-controlled policy branch.
        async with support_engine_iso.begin() as support_conn:
            await _set_support_flag(support_conn)
            context = (
                await support_conn.execute(
                    text("SELECT session_user AS db_role, is_support_session() AS is_support")
                )
            ).one()
            rows = (
                await support_conn.execute(
                    text("SELECT name FROM branch WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            policies_using_flag = (
                await support_conn.execute(text(POLICIES_USING_SUPPORT_FLAG_SQL))
            ).scalar_one()
        assert context.db_role == "aurum_support"
        assert context.is_support is True
        assert sorted(row[0] for row in rows) == ["A-main", "B-main"]
        assert policies_using_flag == 0
    finally:
        # Clean up via support pool — RLS would block deletes from the app side.
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
                await conn.execute(
                    text("DELETE FROM sync_stream WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM sync_writer_epoch WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM sync_node WHERE tenant_id = ANY(:ids)"),
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

            async with maintenance_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM audit_log WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )


async def test_tenant_a_does_not_see_registers_of_tenant_b(
    support_engine_iso: AsyncEngine,
    app_engine_iso: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_ids: list[str] = []
    try:
        async with support_engine_iso.begin() as conn:
            tenant_result = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('Register Iso A', 'register-iso-a@aurum.tj'), "
                    "('Register Iso B', 'register-iso-b@aurum.tj') RETURNING id"
                )
            )
            tenant_ids = [str(row[0]) for row in tenant_result.fetchall()]
            branch_result = await conn.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) VALUES "
                    "(:a, 'Register A branch'), (:b, 'Register B branch') RETURNING id"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1]},
            )
            branch_ids = [str(row[0]) for row in branch_result.fetchall()]
            await conn.execute(
                text(
                    "INSERT INTO register (tenant_id, branch_id, name) VALUES "
                    "(:a, :branch_a, 'A-register'), (:b, :branch_b, 'B-register')"
                ),
                {
                    "a": tenant_ids[0],
                    "b": tenant_ids[1],
                    "branch_a": branch_ids[0],
                    "branch_b": branch_ids[1],
                },
            )

        assert await _visible_register_names(app_engine_iso, tenant_ids, tenant_ids[0]) == [
            "A-register"
        ]
        assert await _visible_register_names(app_engine_iso, tenant_ids, tenant_ids[1]) == [
            "B-register"
        ]
        assert await _visible_register_names(app_engine_iso, tenant_ids, None) == []
    finally:
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
                await conn.execute(
                    text("DELETE FROM register WHERE tenant_id = ANY(:ids)"),
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
            async with maintenance_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM audit_log WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
