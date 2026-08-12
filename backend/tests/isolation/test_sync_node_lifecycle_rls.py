"""Database boundaries for privileged sync-node lifecycle records."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

LIFECYCLE_TABLES = {
    "sync_node_admin_event",
    "sync_node_credential_rotation",
}


@pytest_asyncio.fixture
async def app_engine_sync_lifecycle() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def support_engine_sync_lifecycle() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_sync_node_lifecycle_tables_are_rls_protected_and_not_app_readable(
    app_engine_sync_lifecycle: AsyncEngine,
    support_engine_sync_lifecycle: AsyncEngine,
) -> None:
    async with support_engine_sync_lifecycle.connect() as connection:
        relations = (
            await connection.execute(
                text("""
                    SELECT relname, relrowsecurity
                    FROM pg_catalog.pg_class
                    WHERE relnamespace = 'public'::regnamespace
                      AND relname = ANY(:tables)
                    """),
                {"tables": sorted(LIFECYCLE_TABLES)},
            )
        ).mappings()
        policies = (
            await connection.execute(
                text("""
                    SELECT tablename, policyname
                    FROM pg_catalog.pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = ANY(:tables)
                    """),
                {"tables": sorted(LIFECYCLE_TABLES)},
            )
        ).mappings()
        support_privileges = (
            await connection.execute(
                text("""
                    SELECT relname,
                           has_table_privilege('aurum_support', oid, 'SELECT') AS can_select,
                           has_table_privilege('aurum_support', oid, 'INSERT') AS can_insert,
                           has_table_privilege('aurum_support', oid, 'UPDATE') AS can_update,
                           has_table_privilege('aurum_support', oid, 'DELETE') AS can_delete
                    FROM pg_catalog.pg_class
                    WHERE relnamespace = 'public'::regnamespace
                      AND relname = ANY(:tables)
                    """),
                {"tables": sorted(LIFECYCLE_TABLES)},
            )
        ).mappings()

    assert {row["relname"] for row in relations if row["relrowsecurity"]} == LIFECYCLE_TABLES
    assert {(row["tablename"], row["policyname"]) for row in policies} == {
        (table, "tenant_isolation") for table in LIFECYCLE_TABLES
    }
    assert {row["relname"] for row in support_privileges if row["can_select"]} == LIFECYCLE_TABLES
    assert all(
        not row[privilege]
        for row in support_privileges
        for privilege in ("can_insert", "can_update", "can_delete")
    )

    for table in sorted(LIFECYCLE_TABLES):
        async with app_engine_sync_lifecycle.connect() as connection:
            with pytest.raises(DBAPIError) as error:
                await connection.execute(text(f"SELECT id FROM public.{table} LIMIT 1"))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
