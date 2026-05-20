"""RLS isolation across shift / sale / sale_item / sale_payment."""

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


async def test_sale_invisible_across_tenants(
    support_engine_iso: AsyncEngine, app_engine_iso: AsyncEngine
) -> None:
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    try:
        async with support_engine_iso.begin() as conn:
            tr = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) VALUES "
                    "('IsoPos A','isopos-a@aurum.tj'),('IsoPos B','isopos-b@aurum.tj') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(r[0]) for r in tr.fetchall()]
            br = await conn.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) VALUES " "(:a,'A'),(:b,'B') RETURNING id"
                ),
                {"a": tenant_ids[0], "b": tenant_ids[1]},
            )
            branch_ids = [str(r[0]) for r in br.fetchall()]
            rr = await conn.execute(
                text(
                    "INSERT INTO register (tenant_id, branch_id, name) VALUES "
                    "(:ta,:ba,'R'),(:tb,:bb,'R') RETURNING id"
                ),
                {
                    "ta": tenant_ids[0],
                    "ba": branch_ids[0],
                    "tb": tenant_ids[1],
                    "bb": branch_ids[1],
                },
            )
            register_ids = [str(r[0]) for r in rr.fetchall()]
            ur = await conn.execute(
                text(
                    "INSERT INTO app_user (email, full_name) VALUES "
                    "('cashier-a@aurum.tj','A'),('cashier-b@aurum.tj','B') "
                    "RETURNING id"
                )
            )
            user_ids = [str(r[0]) for r in ur.fetchall()]
            sh = await conn.execute(
                text(
                    "INSERT INTO shift (tenant_id, branch_id, register_id, "
                    "opened_by_user_id) VALUES "
                    "(:ta,:ba,:ra,:ua),(:tb,:bb,:rb,:ub) RETURNING id"
                ),
                {
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "ba": branch_ids[0],
                    "bb": branch_ids[1],
                    "ra": register_ids[0],
                    "rb": register_ids[1],
                    "ua": user_ids[0],
                    "ub": user_ids[1],
                },
            )
            shift_ids = [str(r[0]) for r in sh.fetchall()]
            await conn.execute(
                text(
                    "INSERT INTO sale (tenant_id, branch_id, register_id, "
                    "shift_id, cashier_user_id, status) VALUES "
                    "(:ta,:ba,:ra,:sa,:ua,'completed'),"
                    "(:tb,:bb,:rb,:sb,:ub,'completed')"
                ),
                {
                    "ta": tenant_ids[0],
                    "tb": tenant_ids[1],
                    "ba": branch_ids[0],
                    "bb": branch_ids[1],
                    "ra": register_ids[0],
                    "rb": register_ids[1],
                    "sa": shift_ids[0],
                    "sb": shift_ids[1],
                    "ua": user_ids[0],
                    "ub": user_ids[1],
                },
            )

        async with app_engine_iso.connect() as app_conn:
            await _set_tenant(app_conn, tenant_ids[0])
            rows = (
                await app_conn.execute(
                    text("SELECT id FROM sale WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
            ).fetchall()
            assert len(rows) == 1, "Tenant A must see only its own sale"
    finally:
        if tenant_ids:
            async with support_engine_iso.begin() as conn:
                for tbl in (
                    "sale",
                    "shift",
                    "register",
                    "branch",
                    "tenant_settings",
                ):
                    await conn.execute(
                        text(f"DELETE FROM {tbl} WHERE tenant_id = ANY(:ids)"),
                        {"ids": tenant_ids},
                    )
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                if user_ids:
                    await conn.execute(
                        text("DELETE FROM app_user WHERE id = ANY(:ids)"),
                        {"ids": user_ids},
                    )
