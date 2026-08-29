"""RLS isolation for the roles domain: user_assignment leaks would expose
who works for which tenant.

Tenant A's user_assignment rows must be invisible to Tenant B (and vice
versa) when reading through the app pool. System `role` rows (tenant_id
IS NULL) must remain visible to everyone, because that's the role
catalogue every tenant relies on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_iso() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL_SUPPORT,
        poolclass=NullPool,
        connect_args={"server_settings": {"app.support_session": "true"}},
    )
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
    maintenance_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    role_ids: list[str] = []
    try:
        # ---- seed two tenants + one tenant-scoped role and assignment each ----
        async with support_engine_iso.begin() as conn:
            t_rows = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "(:name_a, :contact_a), (:name_b, :contact_b) "
                    "RETURNING id"
                ),
                {
                    "name_a": f"IsoRoles A {suffix}",
                    "contact_a": f"roles-a-{suffix}@example.invalid",
                    "name_b": f"IsoRoles B {suffix}",
                    "contact_b": f"roles-b-{suffix}@example.invalid",
                },
            )
            tenant_ids = [str(row[0]) for row in t_rows.fetchall()]

            u_rows = await conn.execute(
                text(
                    "INSERT INTO app_user (email, full_name, home_tenant_id) VALUES "
                    "(:email_a, 'A worker', :a), "
                    "(:email_b, 'B worker', :b) "
                    "RETURNING id"
                ),
                {
                    "a": tenant_ids[0],
                    "b": tenant_ids[1],
                    "email_a": f"iso-roles-a-{suffix}@example.invalid",
                    "email_b": f"iso-roles-b-{suffix}@example.invalid",
                },
            )
            user_ids = [str(row[0]) for row in u_rows.fetchall()]

            await conn.execute(
                text(
                    "INSERT INTO tenant_membership "
                    "(tenant_id, user_id, full_name, status) VALUES "
                    "(:ta, :ua, 'A worker', 'active'), "
                    "(:tb, :ub, 'B worker', 'active')"
                ),
                {
                    "ua": user_ids[0],
                    "ub": user_ids[1],
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                },
            )

            role_rows = await conn.execute(
                text(
                    "INSERT INTO role "
                    "(tenant_id, name, level, is_system, is_protected) VALUES "
                    "(:ta, 'Iso worker A', 4, false, false), "
                    "(:tb, 'Iso worker B', 4, false, false) "
                    "RETURNING id"
                ),
                {"ta": tenant_ids[0], "tb": tenant_ids[1]},
            )
            role_ids = [str(row[0]) for row in role_rows.fetchall()]

        async with maintenance_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO access_role_version "
                    "(id, role_id, tenant_id, version, name, description, status, "
                    "creation_xid, published_at, created_by) "
                    "SELECT gen_random_uuid(), id, tenant_id, version, name, "
                    "description, 'published', txid_current(), statement_timestamp(), "
                    "created_by FROM role WHERE id = ANY(CAST(:role_ids AS UUID[]))"
                ),
                {"role_ids": role_ids},
            )

        async with support_engine_iso.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO user_assignment (user_id, tenant_id, role_id) VALUES "
                    "(:ua, :ta, :ra), (:ub, :tb, :rb)"
                ),
                {
                    "ua": user_ids[0],
                    "ub": user_ids[1],
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "ra": role_ids[0],
                    "rb": role_ids[1],
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
            assert names == ["developer", "administrator"]
    finally:
        if user_ids or tenant_ids:
            async with support_engine_iso.begin() as conn:
                if tenant_ids:
                    await conn.execute(
                        text("DELETE FROM user_assignment " "WHERE tenant_id = ANY(:ids)"),
                        {"ids": tenant_ids},
                    )
            if role_ids:
                async with maintenance_engine.begin() as conn:
                    await conn.execute(
                        text(
                            "DELETE FROM access_role_version_permission "
                            "WHERE role_version_id IN ("
                            "SELECT id FROM access_role_version "
                            "WHERE role_id = ANY(CAST(:ids AS UUID[])))"
                        ),
                        {"ids": role_ids},
                    )
                    await conn.execute(
                        text(
                            "DELETE FROM access_role_version "
                            "WHERE role_id = ANY(CAST(:ids AS UUID[]))"
                        ),
                        {"ids": role_ids},
                    )
            async with support_engine_iso.begin() as conn:
                if role_ids:
                    await conn.execute(
                        text("DELETE FROM role WHERE id = ANY(CAST(:ids AS UUID[]))"),
                        {"ids": role_ids},
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
