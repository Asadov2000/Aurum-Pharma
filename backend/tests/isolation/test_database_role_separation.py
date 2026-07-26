"""Contract tests for PostgreSQL runtime and migration role separation."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def app_role_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def support_role_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def migration_role_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["DATABASE_URL_MIGRATION"], poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_database_roles_have_exact_attributes_and_memberships(
    migration_role_engine: AsyncEngine,
) -> None:
    async with migration_role_engine.connect() as connection:
        role_rows = (await connection.execute(text("""
                    SELECT
                      rolname,
                      rolcanlogin,
                      rolinherit,
                      rolsuper,
                      rolcreatedb,
                      rolcreaterole,
                      rolreplication,
                      rolbypassrls
                    FROM pg_catalog.pg_roles
                    WHERE rolname IN (
                      'aurum_app',
                      'aurum_support',
                      'aurum_migrator',
                      'aurum_schema_owner'
                    )
                    ORDER BY rolname
                """))).mappings()
        memberships = (await connection.execute(text("""
                    SELECT
                      granted.rolname AS granted_role,
                      member.rolname AS member_role,
                      membership.admin_option,
                      membership.inherit_option,
                      membership.set_option
                    FROM pg_catalog.pg_auth_members AS membership
                    JOIN pg_catalog.pg_roles AS granted
                      ON granted.oid = membership.roleid
                    JOIN pg_catalog.pg_roles AS member
                      ON member.oid = membership.member
                    WHERE granted.rolname IN (
                      'aurum_schema_owner',
                      'aurum_support'
                    )
                      AND member.rolname = 'aurum_migrator'
                    ORDER BY granted.rolname, member.rolname
                """))).mappings()

    assert {row["rolname"]: dict(row) for row in role_rows} == {
        "aurum_app": {
            "rolname": "aurum_app",
            "rolcanlogin": True,
            "rolinherit": True,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolbypassrls": False,
        },
        "aurum_migrator": {
            "rolname": "aurum_migrator",
            "rolcanlogin": True,
            "rolinherit": True,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolbypassrls": False,
        },
        "aurum_schema_owner": {
            "rolname": "aurum_schema_owner",
            "rolcanlogin": False,
            "rolinherit": True,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolbypassrls": True,
        },
        "aurum_support": {
            "rolname": "aurum_support",
            "rolcanlogin": True,
            "rolinherit": True,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolbypassrls": True,
        },
    }
    assert [dict(row) for row in memberships] == [
        {
            "granted_role": "aurum_schema_owner",
            "member_role": "aurum_migrator",
            "admin_option": False,
            "inherit_option": True,
            "set_option": True,
        },
        {
            "granted_role": "aurum_support",
            "member_role": "aurum_migrator",
            "admin_option": False,
            "inherit_option": True,
            "set_option": True,
        },
    ]


async def test_runtime_roles_have_no_transitive_elevated_membership(
    migration_role_engine: AsyncEngine,
) -> None:
    async with migration_role_engine.connect() as connection:
        memberships = (await connection.execute(text("""
                    SELECT
                      runtime.rolname AS runtime_role,
                      elevated.rolname AS elevated_role,
                      pg_catalog.pg_has_role(
                        runtime.oid,
                        elevated.oid,
                        'MEMBER'
                      ) AS is_member
                    FROM pg_catalog.pg_roles AS runtime
                    CROSS JOIN pg_catalog.pg_roles AS elevated
                    WHERE runtime.rolname IN ('aurum_app', 'aurum_support')
                      AND elevated.rolname IN (
                        'aurum_migrator',
                        'aurum_schema_owner'
                      )
                    ORDER BY runtime.rolname, elevated.rolname
                """))).mappings()

    assert [dict(row) for row in memberships] == [
        {
            "runtime_role": "aurum_app",
            "elevated_role": "aurum_migrator",
            "is_member": False,
        },
        {
            "runtime_role": "aurum_app",
            "elevated_role": "aurum_schema_owner",
            "is_member": False,
        },
        {
            "runtime_role": "aurum_support",
            "elevated_role": "aurum_migrator",
            "is_member": False,
        },
        {
            "runtime_role": "aurum_support",
            "elevated_role": "aurum_schema_owner",
            "is_member": False,
        },
    ]


async def test_runtime_roles_own_no_application_objects(
    migration_role_engine: AsyncEngine,
) -> None:
    async with migration_role_engine.connect() as connection:
        owners = (await connection.execute(text("""
                    SELECT
                      (
                        SELECT pg_catalog.pg_get_userbyid(datdba)
                        FROM pg_catalog.pg_database
                        WHERE datname = current_database()
                      ) AS database_owner,
                      (
                        SELECT pg_catalog.pg_get_userbyid(nspowner)
                        FROM pg_catalog.pg_namespace
                        WHERE nspname = 'public'
                      ) AS schema_owner,
                      (
                        SELECT count(*)
                        FROM pg_catalog.pg_class AS relations
                        JOIN pg_catalog.pg_namespace AS schemas
                          ON schemas.oid = relations.relnamespace
                        JOIN pg_catalog.pg_roles AS roles
                          ON roles.oid = relations.relowner
                        WHERE schemas.nspname = 'public'
                          AND roles.rolname IN ('aurum_app', 'aurum_support')
                      ) AS runtime_owned_relations,
                      (
                        SELECT count(*)
                        FROM pg_catalog.pg_proc AS routines
                        JOIN pg_catalog.pg_namespace AS schemas
                          ON schemas.oid = routines.pronamespace
                        JOIN pg_catalog.pg_roles AS roles
                          ON roles.oid = routines.proowner
                        WHERE schemas.nspname = 'public'
                          AND roles.rolname IN ('aurum_app', 'aurum_support')
                      ) AS runtime_owned_functions
                """))).mappings().one()

    assert owners == {
        "database_owner": "aurum_schema_owner",
        "schema_owner": "aurum_schema_owner",
        "runtime_owned_relations": 0,
        "runtime_owned_functions": 0,
    }


@pytest.mark.parametrize(
    "statement",
    (
        "CREATE TABLE public.support_ddl_probe (id INTEGER)",
        "ALTER TABLE public.tenant ADD COLUMN support_ddl_probe INTEGER",
        "ALTER TABLE public.tenant DISABLE TRIGGER ALL",
        "DROP TABLE public.tenant",
        "TRUNCATE TABLE public.tenant",
    ),
)
async def test_support_cannot_change_schema_or_grants(
    support_role_engine: AsyncEngine,
    statement: str,
) -> None:
    with pytest.raises(DBAPIError) as error:
        async with support_role_engine.begin() as connection:
            await connection.execute(text(statement))
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


async def test_runtime_roles_cannot_assume_migration_roles(
    app_role_engine: AsyncEngine,
    support_role_engine: AsyncEngine,
) -> None:
    for engine in (app_role_engine, support_role_engine):
        for target_role in ("aurum_migrator", "aurum_schema_owner"):
            with pytest.raises(DBAPIError) as error:
                async with engine.begin() as connection:
                    await connection.execute(text(f"SET ROLE {target_role}"))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"


async def test_support_cannot_grant_runtime_privileges(
    support_role_engine: AsyncEngine,
) -> None:
    async with support_role_engine.begin() as connection:
        await connection.execute(text("GRANT TRUNCATE ON TABLE public.tenant TO aurum_app"))
        app_can_truncate = await connection.scalar(text("""
                SELECT pg_catalog.has_table_privilege(
                  'aurum_app', 'public.tenant', 'TRUNCATE'
                )
            """))

    assert app_can_truncate is False


async def test_support_has_only_runtime_database_and_schema_rights(
    support_role_engine: AsyncEngine,
) -> None:
    async with support_role_engine.connect() as connection:
        privileges = (await connection.execute(text("""
                    SELECT
                      pg_catalog.has_database_privilege(
                        current_user, current_database(), 'CONNECT'
                      ) AS can_connect,
                      pg_catalog.has_database_privilege(
                        current_user, current_database(), 'CREATE'
                      ) AS can_create_database_object,
                      pg_catalog.has_database_privilege(
                        current_user, current_database(), 'TEMP'
                      ) AS can_create_temp,
                      pg_catalog.has_schema_privilege(
                        current_user, 'public', 'USAGE'
                      ) AS can_use_schema,
                      pg_catalog.has_schema_privilege(
                        current_user, 'public', 'CREATE'
                      ) AS can_create_in_schema,
                      pg_catalog.has_table_privilege(
                        current_user, 'public.tenant', 'TRUNCATE'
                      ) AS can_truncate,
                      pg_catalog.has_table_privilege(
                        current_user, 'public.tenant', 'TRIGGER'
                      ) AS can_manage_triggers,
                      pg_catalog.has_table_privilege(
                        current_user, 'public.audit_log', 'SELECT'
                      ) AS can_read_audit,
                      pg_catalog.has_table_privilege(
                        current_user, 'public.audit_log', 'UPDATE'
                      ) AS can_update_audit,
                      pg_catalog.has_table_privilege(
                        current_user, 'public.alembic_version', 'SELECT'
                      ) AS can_read_alembic
                """))).mappings().one()

    assert privileges == {
        "can_connect": True,
        "can_create_database_object": False,
        "can_create_temp": False,
        "can_use_schema": True,
        "can_create_in_schema": False,
        "can_truncate": False,
        "can_manage_triggers": False,
        "can_read_audit": True,
        "can_update_audit": False,
        "can_read_alembic": False,
    }


async def test_owner_objects_are_private_by_default(
    migration_role_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    table_name = f"owner_default_table_{suffix}"
    function_name = f"owner_default_function_{suffix}"

    async with migration_role_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
            await connection.execute(text(f"CREATE TABLE public.{table_name} (id INTEGER)"))
            await connection.execute(
                text(
                    f"CREATE FUNCTION public.{function_name}() RETURNS INTEGER "
                    "LANGUAGE SQL AS 'SELECT 1'"
                )
            )
            privileges = (
                (
                    await connection.execute(
                        text("""
                        SELECT
                          pg_catalog.has_table_privilege(
                            'aurum_app', :table_name, 'SELECT'
                          ) AS app_can_read_table,
                          pg_catalog.has_table_privilege(
                            'aurum_support', :table_name, 'SELECT'
                          ) AS support_can_read_table,
                          pg_catalog.has_function_privilege(
                            'aurum_app', :function_name, 'EXECUTE'
                          ) AS app_can_execute_function,
                          pg_catalog.has_function_privilege(
                            'aurum_support', :function_name, 'EXECUTE'
                          ) AS support_can_execute_function
                    """),
                        {
                            "table_name": f"public.{table_name}",
                            "function_name": f"public.{function_name}()",
                        },
                    )
                )
                .mappings()
                .one()
            )
        finally:
            await transaction.rollback()

    assert privileges == {
        "app_can_read_table": False,
        "support_can_read_table": False,
        "app_can_execute_function": False,
        "support_can_execute_function": False,
    }
