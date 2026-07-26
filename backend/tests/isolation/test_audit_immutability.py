"""Database-level write boundary for the immutable audit ledger."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

AUDIT_TABLE_PRIVILEGES_SQL = """
SELECT
  has_table_privilege('aurum_app', 'public.audit_log', 'SELECT') AS can_select,
  has_table_privilege('aurum_app', 'public.audit_log', 'INSERT') AS can_insert,
  has_table_privilege('aurum_app', 'public.audit_log', 'UPDATE') AS can_update,
  has_table_privilege('aurum_app', 'public.audit_log', 'DELETE') AS can_delete
"""

AUDIT_FUNCTION_SECURITY_SQL = """
SELECT
  has_schema_privilege('aurum_app', 'public', 'CREATE') AS can_create,
  has_function_privilege(
    'aurum_app',
    'public.trg_audit_log()',
    'EXECUTE'
  ) AS can_execute_trigger,
  has_function_privilege(
    'aurum_app',
    'public.append_audit_event(uuid,uuid,text,text,uuid,jsonb)',
    'EXECUTE'
  ) AS can_append,
  p.prosecdef AS trigger_is_security_definer,
  p.proconfig AS trigger_config
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname = 'trg_audit_log'
"""


@pytest_asyncio.fixture
async def support_engine_audit() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_audit() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_tenant(conn: AsyncConnection, tenant_id: str) -> None:
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
        {"tenant_id": tenant_id},
    )


async def test_audit_privileges_are_least_privilege(
    support_engine_audit: AsyncEngine,
) -> None:
    async with support_engine_audit.connect() as conn:
        table_privileges = (await conn.execute(text(AUDIT_TABLE_PRIVILEGES_SQL))).one()
        security = (await conn.execute(text(AUDIT_FUNCTION_SECURITY_SQL))).one()

    assert table_privileges.can_select is True
    assert table_privileges.can_insert is False
    assert table_privileges.can_update is False
    assert table_privileges.can_delete is False
    assert security.can_create is False
    assert security.can_execute_trigger is False
    assert security.can_append is True
    assert security.trigger_is_security_definer is True
    assert security.trigger_config == ["search_path=pg_catalog, public, pg_temp"]


async def test_app_uses_validated_append_and_cannot_tamper(
    support_engine_audit: AsyncEngine,
    app_engine_audit: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id: str | None = None
    branch_id: str | None = None
    event_id: str | None = None
    nick = uuid4().hex[:10]

    try:
        async with support_engine_audit.begin() as conn:
            tenant_id = str(
                (
                    await conn.execute(
                        text("""
                            INSERT INTO tenant (name, contact_email)
                            VALUES (:name, :email)
                            RETURNING id
                            """),
                        {
                            "name": f"Audit security {nick}",
                            "email": f"audit-security-{nick}@aurum.tj",
                        },
                    )
                ).scalar_one()
            )

        async with app_engine_audit.begin() as conn:
            await _set_tenant(conn, tenant_id)
            branch_id = str(
                (
                    await conn.execute(
                        text("""
                            INSERT INTO branch (tenant_id, name)
                            VALUES (CAST(:tenant_id AS UUID), :name)
                            RETURNING id
                            """),
                        {"tenant_id": tenant_id, "name": "Audited branch"},
                    )
                ).scalar_one()
            )
            event_id = str(
                (
                    await conn.execute(
                        text("""
                            SELECT public.append_audit_event(
                              CAST(:tenant_id AS UUID),
                              NULL,
                              'VIEW',
                              'branch',
                              CAST(:branch_id AS UUID),
                              jsonb_build_object('reason', 'security test')
                            )
                            """),
                        {"tenant_id": tenant_id, "branch_id": branch_id},
                    )
                ).scalar_one()
            )

            actions = set(
                (
                    await conn.execute(
                        text("""
                            SELECT action
                            FROM audit_log
                            WHERE tenant_id = CAST(:tenant_id AS UUID)
                              AND table_name = 'branch'
                              AND record_id = CAST(:branch_id AS UUID)
                            """),
                        {"tenant_id": tenant_id, "branch_id": branch_id},
                    )
                ).scalars()
            )
            assert {"INSERT", "VIEW"}.issubset(actions)

        with pytest.raises(DBAPIError):
            async with app_engine_audit.begin() as conn:
                await _set_tenant(conn, tenant_id)
                await conn.execute(
                    text("""
                        INSERT INTO audit_log (tenant_id, action, table_name)
                        VALUES (CAST(:tenant_id AS UUID), 'VIEW', 'forged')
                        """),
                    {"tenant_id": tenant_id},
                )

        with pytest.raises(DBAPIError):
            async with app_engine_audit.begin() as conn:
                await _set_tenant(conn, tenant_id)
                await conn.execute(
                    text("""
                        UPDATE audit_log
                        SET table_name = 'tampered'
                        WHERE id = CAST(:event_id AS UUID)
                        """),
                    {"event_id": event_id},
                )

        with pytest.raises(DBAPIError):
            async with app_engine_audit.begin() as conn:
                await _set_tenant(conn, tenant_id)
                await conn.execute(
                    text("DELETE FROM audit_log WHERE id = CAST(:event_id AS UUID)"),
                    {"event_id": event_id},
                )
    finally:
        if tenant_id is not None:
            async with support_engine_audit.begin() as conn:
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = CAST(:tenant_id AS UUID)"),
                    {"tenant_id": tenant_id},
                )

            async with maintenance_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM audit_log " "WHERE tenant_id = CAST(:tenant_id AS UUID)"),
                    {"tenant_id": tenant_id},
                )
