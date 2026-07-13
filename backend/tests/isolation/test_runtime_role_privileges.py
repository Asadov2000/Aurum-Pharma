"""Database privileges that keep the runtime role inside row-level controls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

CRUD_TABLES = {
    "app_user",
    "barcode",
    "batch",
    "batch_movement",
    "branch",
    "catalog_import_job",
    "email_code",
    "incoming_document",
    "incoming_item",
    "invoice",
    "login_attempt",
    "notification",
    "notification_subscription",
    "onboarding_checklist",
    "payment",
    "prescription_log",
    "register",
    "role",
    "role_permission",
    "sale",
    "sale_item",
    "sale_payment",
    "session",
    "shift",
    "supplier",
    "supplier_return",
    "tenant",
    "tenant_catalog",
    "tenant_settings",
    "tenant_subscription",
    "user_assignment",
    "wizard_state",
    "write_off",
}

READ_ONLY_TABLES = {
    "audit_log",
    "master_catalog",
    "permission",
    "role_template",
    "role_template_permission",
    "subscription_plan",
}

NO_ACCESS_TABLES = {"alembic_version", "notification_delivery"}

RUNTIME_VIEWS = {
    "v_active_subscription",
    "v_batch_with_expiry_status",
}

CUSTOM_FUNCTIONS = {
    "append_audit_event",
    "audit_redact_jsonb",
    "current_app_user_id",
    "current_tenant_id",
    "enqueue_notification_delivery",
    "is_support_session",
    "resolve_notification_subscription",
    "trg_audit_log",
    "trg_set_created_meta",
    "trg_set_updated_meta",
    "trg_update_batch_qty",
}

APP_EXECUTABLE_FUNCTIONS = {
    "append_audit_event",
    "current_app_user_id",
    "current_tenant_id",
    "enqueue_notification_delivery",
    "is_support_session",
    "resolve_notification_subscription",
}

APP_EXECUTABLE_EXTENSION_FUNCTIONS = {
    ("pg_trgm", "public.similarity_op(text, text)"),
    ("pgcrypto", "public.gen_random_uuid()"),
}

RELATION_PRIVILEGES_SQL = """
SELECT
  relations.relname,
  checks.privilege,
  pg_catalog.has_table_privilege(
    'aurum_app', relations.oid, checks.privilege
  ) AS has_privilege,
  pg_catalog.has_table_privilege(
    'aurum_app',
    relations.oid,
    checks.privilege || ' WITH GRANT OPTION'
  ) AS is_grantable
FROM pg_catalog.pg_class AS relations
JOIN pg_catalog.pg_namespace AS schemas
  ON schemas.oid = relations.relnamespace
CROSS JOIN (
  VALUES
    ('SELECT'),
    ('INSERT'),
    ('UPDATE'),
    ('DELETE'),
    ('TRUNCATE'),
    ('REFERENCES'),
    ('TRIGGER')
) AS checks(privilege)
WHERE schemas.nspname = 'public'
  AND relations.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY relations.relname, checks.privilege
"""

DEFAULT_PRIVILEGES_SQL = """
WITH protected_owners AS (
  SELECT roles.oid, roles.rolname
  FROM pg_catalog.pg_roles AS roles
  WHERE roles.rolname IN (
    'aurum_support',
    (
      SELECT pg_catalog.pg_get_userbyid(databases.datdba)
      FROM pg_catalog.pg_database AS databases
      WHERE databases.datname = current_database()
    )
  )
),
object_types(object_type) AS (
  VALUES ('r'::"char"), ('S'::"char"), ('f'::"char")
),
unsafe_defaults AS (
  SELECT
    owners.rolname AS owner,
    object_types.object_type,
    COALESCE(grantees.rolname, 'PUBLIC') AS grantee,
    acl.privilege_type
  FROM protected_owners AS owners
  CROSS JOIN object_types
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(
      (
        SELECT defaults.defaclacl
        FROM pg_catalog.pg_default_acl AS defaults
        WHERE defaults.defaclrole = owners.oid
          AND defaults.defaclnamespace = 0
          AND defaults.defaclobjtype = object_types.object_type
      ),
      pg_catalog.acldefault(object_types.object_type, owners.oid)
    )
  ) AS acl
  LEFT JOIN pg_catalog.pg_roles AS grantees
    ON grantees.oid = acl.grantee
  WHERE acl.grantee = 0 OR grantees.rolname = 'aurum_app'

  UNION ALL

  SELECT
    owners.rolname AS owner,
    defaults.defaclobjtype AS object_type,
    COALESCE(grantees.rolname, 'PUBLIC') AS grantee,
    acl.privilege_type
  FROM pg_catalog.pg_default_acl AS defaults
  JOIN protected_owners AS owners
    ON owners.oid = defaults.defaclrole
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = defaults.defaclnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS acl
  LEFT JOIN pg_catalog.pg_roles AS grantees
    ON grantees.oid = acl.grantee
  WHERE schemas.nspname = 'public'
    AND defaults.defaclobjtype IN ('r', 'S', 'f')
    AND (acl.grantee = 0 OR grantees.rolname = 'aurum_app')
)
SELECT
  owner,
  object_type,
  grantee,
  privilege_type
FROM unsafe_defaults
ORDER BY owner, object_type, grantee, privilege_type
"""

DATABASE_PRIVILEGES_SQL = """
SELECT
  pg_catalog.has_database_privilege(
    'aurum_app', current_database(), 'CONNECT'
  ) AS app_can_connect,
  pg_catalog.has_database_privilege(
    'aurum_app', current_database(), 'CREATE'
  ) AS app_can_create,
  pg_catalog.has_database_privilege(
    'aurum_app', current_database(), 'TEMP'
  ) AS app_can_create_temp,
  EXISTS (
    SELECT 1
    FROM pg_catalog.pg_database AS databases
    CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(
        databases.datacl,
        pg_catalog.acldefault('d', databases.datdba)
      )
    ) AS acl
    WHERE databases.datname = current_database()
      AND acl.grantee = 0
  ) AS public_has_privileges
"""

RUNTIME_VIEW_SECURITY_SQL = """
SELECT
  relations.relname,
  pg_catalog.pg_get_userbyid(relations.relowner) AS owner,
  COALESCE(relations.reloptions, ARRAY[]::TEXT[]) AS options
FROM pg_catalog.pg_class AS relations
JOIN pg_catalog.pg_namespace AS schemas
  ON schemas.oid = relations.relnamespace
WHERE schemas.nspname = 'public'
  AND relations.relkind = 'v'
ORDER BY relations.relname
"""

CUSTOM_FUNCTION_PRIVILEGES_SQL = """
SELECT
  routines.proname,
  routines.prosecdef AS is_security_definer,
  pg_catalog.has_function_privilege(
    'aurum_app', routines.oid, 'EXECUTE'
  ) AS app_can_execute,
  pg_catalog.has_function_privilege(
    'aurum_app', routines.oid, 'EXECUTE WITH GRANT OPTION'
  ) AS is_grantable,
  EXISTS (
    SELECT 1
    FROM pg_catalog.aclexplode(
      COALESCE(
        routines.proacl,
        pg_catalog.acldefault('f'::"char", routines.proowner)
      )
    ) AS privileges
    WHERE privileges.grantee = 0
      AND privileges.privilege_type = 'EXECUTE'
  ) AS public_can_execute
FROM pg_catalog.pg_proc AS routines
JOIN pg_catalog.pg_namespace AS schemas
  ON schemas.oid = routines.pronamespace
JOIN pg_catalog.pg_roles AS owners
  ON owners.oid = routines.proowner
WHERE schemas.nspname = 'public'
  AND owners.rolname = 'aurum_support'
ORDER BY routines.proname
"""

APP_EXECUTABLE_SECURITY_DEFINERS_SQL = """
SELECT routines.proname
FROM pg_catalog.pg_proc AS routines
JOIN pg_catalog.pg_namespace AS schemas
  ON schemas.oid = routines.pronamespace
WHERE schemas.nspname = 'public'
  AND routines.prosecdef
  AND pg_catalog.has_function_privilege('aurum_app', routines.oid, 'EXECUTE')
ORDER BY routines.proname
"""

EXTENSION_FUNCTION_PRIVILEGES_SQL = """
SELECT
  extensions.extname,
  pg_catalog.format(
    '%I.%I(%s)',
    schemas.nspname,
    routines.proname,
    pg_catalog.pg_get_function_identity_arguments(routines.oid)
  ) AS signature,
  pg_catalog.has_function_privilege(
    'aurum_app', routines.oid, 'EXECUTE'
  ) AS app_can_execute,
  pg_catalog.has_function_privilege(
    'aurum_app', routines.oid, 'EXECUTE WITH GRANT OPTION'
  ) AS is_grantable
FROM pg_catalog.pg_proc AS routines
JOIN pg_catalog.pg_namespace AS schemas
  ON schemas.oid = routines.pronamespace
JOIN pg_catalog.pg_depend AS dependencies
  ON dependencies.classid = 'pg_proc'::REGCLASS
 AND dependencies.objid = routines.oid
 AND dependencies.deptype = 'e'
JOIN pg_catalog.pg_extension AS extensions
  ON extensions.oid = dependencies.refobjid
WHERE extensions.extname IN ('pg_trgm', 'pgcrypto', 'unaccent')
ORDER BY extensions.extname, signature
"""

RUNTIME_SEQUENCE_PRIVILEGES_SQL = """
SELECT relations.relname, checks.privilege
FROM pg_catalog.pg_class AS relations
JOIN pg_catalog.pg_namespace AS schemas
  ON schemas.oid = relations.relnamespace
CROSS JOIN (
  VALUES ('USAGE'), ('SELECT'), ('UPDATE')
) AS checks(privilege)
WHERE schemas.nspname = 'public'
  AND relations.relkind = 'S'
  AND (
    pg_catalog.has_sequence_privilege(
      'aurum_app', relations.oid, checks.privilege
    )
    OR pg_catalog.has_sequence_privilege(
      'aurum_app',
      relations.oid,
      checks.privilege || ' WITH GRANT OPTION'
    )
  )
ORDER BY relations.relname, checks.privilege
"""


@pytest_asyncio.fixture
async def support_engine_privileges() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_privileges() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_runtime_role_has_only_row_level_table_privileges(
    support_engine_privileges: AsyncEngine,
) -> None:
    async with support_engine_privileges.connect() as conn:
        relation_result = await conn.execute(text(RELATION_PRIVILEGES_SQL))
        relation_privileges = list(relation_result.mappings())
        defaults_result = await conn.execute(text(DEFAULT_PRIVILEGES_SQL))
        default_privileges = list(defaults_result.tuples())
        database_result = await conn.execute(text(DATABASE_PRIVILEGES_SQL))
        database_privileges = database_result.mappings().one()
        view_security_result = await conn.execute(text(RUNTIME_VIEW_SECURITY_SQL))
        view_security = list(view_security_result.mappings())
        function_result = await conn.execute(text(CUSTOM_FUNCTION_PRIVILEGES_SQL))
        function_privileges = list(function_result.mappings())
        definer_result = await conn.execute(text(APP_EXECUTABLE_SECURITY_DEFINERS_SQL))
        executable_definers = list(definer_result.scalars())
        extension_result = await conn.execute(text(EXTENSION_FUNCTION_PRIVILEGES_SQL))
        extension_privileges = list(extension_result.mappings())
        sequence_result = await conn.execute(text(RUNTIME_SEQUENCE_PRIVILEGES_SQL))
        sequence_privileges = list(sequence_result.tuples())

    expected_relations = {
        **{table: {"SELECT", "INSERT", "UPDATE", "DELETE"} for table in CRUD_TABLES},
        **{table: {"SELECT"} for table in READ_ONLY_TABLES | RUNTIME_VIEWS},
        **{table: set() for table in NO_ACCESS_TABLES},
    }
    actual_relations: dict[str, set[str]] = {relation: set() for relation in expected_relations}
    for row in relation_privileges:
        assert row["relname"] in expected_relations
        assert row["is_grantable"] is False
        if row["has_privilege"]:
            actual_relations[row["relname"]].add(row["privilege"])

    assert actual_relations == expected_relations
    assert default_privileges == []
    assert database_privileges == {
        "app_can_connect": True,
        "app_can_create": False,
        "app_can_create_temp": False,
        "public_has_privileges": False,
    }
    assert {row["relname"] for row in view_security} == RUNTIME_VIEWS
    assert all(row["owner"] == "aurum_support" for row in view_security)
    assert all("security_invoker=true" in row["options"] for row in view_security)
    assert {row["proname"] for row in function_privileges} == CUSTOM_FUNCTIONS
    assert {
        row["proname"] for row in function_privileges if row["app_can_execute"]
    } == APP_EXECUTABLE_FUNCTIONS
    assert all(row["is_grantable"] is False for row in function_privileges)
    assert all(row["public_can_execute"] is False for row in function_privileges)
    assert executable_definers == [
        "append_audit_event",
        "enqueue_notification_delivery",
        "resolve_notification_subscription",
    ]
    assert {
        (row["extname"], row["signature"]) for row in extension_privileges if row["app_can_execute"]
    } == APP_EXECUTABLE_EXTENSION_FUNCTIONS
    assert all(row["is_grantable"] is False for row in extension_privileges)
    assert sequence_privileges == []


async def _assert_insufficient_privilege(engine: AsyncEngine, statement: str) -> None:
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            with pytest.raises(DBAPIError) as error:
                await conn.execute(text(statement))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def test_runtime_role_cannot_escape_row_level_controls(
    app_engine_privileges: AsyncEngine,
) -> None:
    await _assert_insufficient_privilege(
        app_engine_privileges,
        "TRUNCATE TABLE public.sale CASCADE",
    )
    schema_name = f"runtime_privilege_probe_{uuid4().hex}"
    await _assert_insufficient_privilege(
        app_engine_privileges,
        f"CREATE SCHEMA {schema_name}",
    )
    await _assert_insufficient_privilege(
        app_engine_privileges,
        "SELECT public.digest('probe', 'sha256')",
    )


async def test_runtime_role_can_use_required_extension_functions(
    app_engine_privileges: AsyncEngine,
) -> None:
    async with app_engine_privileges.connect() as conn:
        result = (await conn.execute(text("""
                    SELECT
                      'aspirin' % 'aspirin' AS trigram_matches,
                      public.gen_random_uuid() IS NOT NULL AS uuid_generated
                    """))).mappings().one()

    assert result == {"trigram_matches": True, "uuid_generated": True}


async def test_new_support_objects_are_private_by_default(
    support_engine_privileges: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    table_name = f"runtime_default_table_{suffix}"
    sequence_name = f"runtime_default_sequence_{suffix}"
    function_name = f"runtime_default_function_{suffix}"

    async with support_engine_privileges.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text(f"CREATE TABLE public.{table_name} (id INTEGER)"))
            await conn.execute(text(f"CREATE SEQUENCE public.{sequence_name}"))
            await conn.execute(
                text(
                    f"CREATE FUNCTION public.{function_name}() RETURNS INTEGER "
                    "LANGUAGE SQL AS 'SELECT 1'"
                )
            )
            result = (
                (
                    await conn.execute(
                        text("""
                        SELECT
                          EXISTS (
                            SELECT 1
                            FROM pg_catalog.unnest(ARRAY[
                              'SELECT', 'INSERT', 'UPDATE', 'DELETE',
                              'TRUNCATE', 'REFERENCES', 'TRIGGER'
                            ]) AS checks(privilege)
                            WHERE pg_catalog.has_table_privilege(
                              'aurum_app', :table_name, checks.privilege
                            )
                          ) AS app_has_table_privilege,
                          EXISTS (
                            SELECT 1
                            FROM pg_catalog.unnest(
                              ARRAY['USAGE', 'SELECT', 'UPDATE']
                            ) AS checks(privilege)
                            WHERE pg_catalog.has_sequence_privilege(
                              'aurum_app', :sequence_name, checks.privilege
                            )
                          ) AS app_has_sequence_privilege,
                          pg_catalog.has_function_privilege(
                            'aurum_app', :function_name, 'EXECUTE'
                          ) AS app_can_execute_function
                        """),
                        {
                            "table_name": f"public.{table_name}",
                            "sequence_name": f"public.{sequence_name}",
                            "function_name": f"public.{function_name}()",
                        },
                    )
                )
                .mappings()
                .one()
            )

            assert result == {
                "app_has_table_privilege": False,
                "app_has_sequence_privilege": False,
                "app_can_execute_function": False,
            }
        finally:
            await transaction.rollback()


async def test_runtime_views_apply_invoker_tenant_rls(
    support_engine_privileges: AsyncEngine,
    app_engine_privileges: AsyncEngine,
) -> None:
    tenant_ids: list[str] = []
    nick = uuid4().hex[:10]

    try:
        async with support_engine_privileges.begin() as conn:
            plan_id = str(
                (
                    await conn.execute(
                        text("SELECT id FROM subscription_plan ORDER BY created_at LIMIT 1")
                    )
                ).scalar_one()
            )
            for index in range(2):
                tenant_id = str(
                    (
                        await conn.execute(
                            text("""
                                INSERT INTO tenant (name, contact_email)
                                VALUES (:name, :email)
                                RETURNING id
                                """),
                            {
                                "name": f"View isolation {nick}-{index}",
                                "email": f"view-isolation-{nick}-{index}@aurum.tj",
                            },
                        )
                    ).scalar_one()
                )
                tenant_ids.append(tenant_id)
                await conn.execute(
                    text("""
                        INSERT INTO tenant_subscription (
                          tenant_id,
                          plan_id,
                          status,
                          period_end,
                          branches_count,
                          amount
                        ) VALUES (
                          CAST(:tenant_id AS UUID),
                          CAST(:plan_id AS UUID),
                          'active',
                          now() + INTERVAL '30 days',
                          1,
                          0
                        )
                        """),
                    {"tenant_id": tenant_id, "plan_id": plan_id},
                )

        async with app_engine_privileges.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
                {"tenant_id": tenant_ids[0]},
            )
            visible_tenants = {
                str(tenant_id)
                for tenant_id in (
                    await conn.execute(
                        text("""
                            SELECT tenant_id
                            FROM public.v_active_subscription
                            WHERE tenant_id IN (
                              CAST(:first_tenant_id AS UUID),
                              CAST(:second_tenant_id AS UUID)
                            )
                            """),
                        {
                            "first_tenant_id": tenant_ids[0],
                            "second_tenant_id": tenant_ids[1],
                        },
                    )
                ).scalars()
            }

        assert visible_tenants == {tenant_ids[0]}
    finally:
        if tenant_ids:
            async with support_engine_privileges.begin() as conn:
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(CAST(:tenant_ids AS UUID[]))"),
                    {"tenant_ids": tenant_ids},
                )
                await conn.execute(
                    text(
                        "DELETE FROM audit_log "
                        "WHERE tenant_id = ANY(CAST(:tenant_ids AS UUID[]))"
                    ),
                    {"tenant_ids": tenant_ids},
                )
