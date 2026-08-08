"""Database privileges that keep the runtime role inside row-level controls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import hash_code, hash_password, hash_token

CRUD_TABLES = {
    "barcode",
    "batch",
    "branch",
    "catalog_import_job",
    "incoming_document",
    "incoming_item",
    "invoice",
    "notification_subscription",
    "onboarding_checklist",
    "payment",
    "pos_favorite",
    "prescription_log",
    "register",
    "role",
    "role_permission",
    "sale",
    "sale_item",
    "sale_payment",
    "shift",
    "supplier",
    "tenant",
    "tenant_catalog",
    "tenant_settings",
    "tenant_subscription",
    "wizard_state",
}

APPEND_ONLY_TABLES = {
    "batch_movement",
    "supplier_return",
    "write_off",
}

READ_ONLY_TABLES = {
    "authorization_policy_revision",
    "authorization_subject_revision",
    "audit_log",
    "master_catalog",
    "notification",
    "permission",
    "register_receipt_counter",
    "role_template",
    "role_template_permission",
    "subscription_plan",
    "sync_activation_bootstrap",
    "sync_activation_bootstrap_chunk",
    "sync_activation_bootstrap_component",
    "sync_activation_foundation",
    "sync_cursor",
    "sync_inbox",
    "sync_outbox",
    "sync_sale_projection",
    "sync_shadow_report",
    "sync_stream",
    "sync_writer_activation",
    "sync_writer_epoch",
    "sync_writer_readiness",
    "tenant_membership",
    "tenant_ownership",
    "user_assignment",
}

NO_ACCESS_TABLES = {
    "alembic_version",
    "app_user",
    "auth_mfa_challenge",
    "email_code",
    "login_attempt",
    "notification_delivery",
    "platform_access_grant",
    "session",
    "support_mfa",
    "support_mfa_recovery_code",
    "support_access_capability",
    "support_access_session",
    "sync_node",
}

APP_USER_SAFE_COLUMNS = {
    "id",
    "email",
    "email_lower",
    "full_name",
    "phone",
    "home_tenant_id",
    "status",
    "last_login_at",
}

SYNC_OUTBOX_INSERT_COLUMNS = {
    "event_id",
    "tenant_id",
    "branch_id",
    "operation_id",
    "aggregate_type",
    "aggregate_id",
    "event_type",
    "schema_version",
    "payload",
    "payload_hash",
    "origin_node_id",
    "writer_epoch",
    "sequence",
    "occurred_at",
    "stream_checksum",
    "projection_hash",
    "projection_checksum",
}

RUNTIME_VIEWS = {
    "v_active_subscription",
    "v_batch_with_expiry_status",
}

CUSTOM_FUNCTIONS = {
    "accept_tenant_invitation",
    "activate_edge_writer_handover",
    "allocate_register_receipt",
    "append_audit_event",
    "assert_current_branch_writer",
    "authenticate_edge_node",
    "auth_email_code_matches",
    "audit_redact_jsonb",
    "bump_all_authorization_policy_revisions",
    "bump_authorization_policy_revision",
    "bump_authorization_subject_revision",
    "bump_authorization_subjects_for_user",
    "current_app_user_id",
    "current_tenant_id",
    "create_invited_app_user",
    "create_auth_session_from_email_code",
    "create_scoped_notification",
    "create_tenant_user_assignment",
    "consume_auth_email_code",
    "complete_auth_mfa_enrollment",
    "complete_auth_mfa_verification",
    "complete_support_mfa_step_up",
    "create_auth_mfa_challenge_from_email_code",
    "cancel_edge_writer_handover",
    "deactivate_tenant_user_assignment",
    "decrypt_support_mfa_secret",
    "enforce_auth_login_guard",
    "enqueue_notification_delivery",
    "ensure_authorization_policy_revision",
    "find_invitable_user_id",
    "find_auth_email_code_challenge",
    "issue_auth_email_code",
    "initialize_branch_sync_writer",
    "is_cash_sale_v1_bootstrap_complete",
    "is_support_session",
    "is_tenant_support_session",
    "lookup_auth_user_by_id",
    "lookup_auth_mfa_challenge",
    "lookup_auth_sessions",
    "lookup_auth_session_mfa",
    "lookup_login_user_by_email",
    "lookup_support_mfa_for_step_up",
    "mark_all_scoped_notifications_read",
    "mark_scoped_notification_read",
    "reactivate_tenant_user_assignment",
    "register_auth_session_device",
    "record_edge_writer_readiness",
    "record_auth_login_attempt",
    "record_auth_mfa_failure",
    "record_role_permission_change",
    "reserve_sync_event_position",
    "resolve_notification_subscription",
    "rotate_support_mfa_encryption",
    "recover_auth_mfa_challenge",
    "revoke_auth_session_by_id",
    "revoke_auth_session_by_hash",
    "revoke_other_auth_sessions",
    "revoke_tenant_user_auth_sessions",
    "rotate_auth_session",
    "finalize_sync_event_position",
    "set_tenant_user_status",
    "set_tenant_membership_status",
    "support_access_has_capability",
    "support_actor_can_delegate_permission",
    "stage_auth_mfa_enrollment",
    "tenant_actor_can_delegate_role",
    "tenant_actor_has_permission",
    "tenant_actor_has_scoped_permission",
    "tenant_actor_is_owner",
    "touch_auth_user_last_login",
    "prepare_edge_writer_handover",
    "prepare_edge_writer_foundation_handover",
    "trg_audit_log",
    "trg_audit_platform_access_grant",
    "trg_audit_tenant_membership_event",
    "trg_audit_tenant_ownership_event",
    "trg_authorization_assignment_mutation",
    "trg_authorization_membership_mutation",
    "trg_authorization_ownership_mutation",
    "trg_authorization_permission_mutation",
    "trg_authorization_policy_mutation",
    "trg_authorization_role_permission_mutation",
    "trg_authorization_tenant_created",
    "trg_authorization_user_status_mutation",
    "trg_capture_bootstrap_platform_access",
    "trg_guard_app_user_platform_flags",
    "trg_guard_batch_movement_immutability",
    "trg_guard_write_off_immutability",
    "trg_guard_supplier_return_immutability",
    "trg_revoke_support_access_on_tenant_archive",
    "trg_guard_branch_writer",
    "trg_guard_platform_account_tenant_scope",
    "trg_guard_platform_access_grant",
    "trg_guard_platform_account_status",
    "trg_guard_platform_membership_scope",
    "trg_guard_role_permission_mutation",
    "trg_guard_sale_child_immutability",
    "trg_guard_sale_immutability",
    "trg_require_sale_receipt_snapshot",
    "trg_guard_user_assignment_scope",
    "trg_guard_sync_activation_bootstrap",
    "trg_guard_sync_activation_bootstrap_chunk",
    "trg_guard_sync_activation_bootstrap_component",
    "trg_guard_sync_activation_foundation",
    "trg_guard_sync_outbox_writer",
    "trg_guard_sync_stream_scope",
    "trg_guard_sync_writer_activation",
    "trg_guard_sync_writer_epoch",
    "trg_guard_sync_writer_readiness",
    "trg_guard_tenant_membership",
    "trg_guard_tenant_ownership",
    "trg_guard_tenant_role_mutation",
    "trg_initialize_branch_sync_writer",
    "trg_project_platform_access_grant",
    "trg_require_full_activation_bootstrap",
    "trg_require_full_bootstrap_transition",
    "trg_require_complete_full_activation_bootstrap",
    "trg_revoke_platform_access_for_account",
    "trg_sync_stream_epoch_ledger",
    "trg_set_created_meta",
    "trg_set_updated_meta",
    "trg_update_batch_qty",
    "trg_copy_session_mfa_verification",
    "trg_validate_sync_stream_checkpoint",
    "update_tenant_membership_profile",
    "update_tenant_user_profile",
}

APP_EXECUTABLE_FUNCTIONS = {
    "accept_tenant_invitation",
    "allocate_register_receipt",
    "append_audit_event",
    "authenticate_edge_node",
    "auth_email_code_matches",
    "current_app_user_id",
    "current_tenant_id",
    "create_auth_session_from_email_code",
    "create_scoped_notification",
    "create_tenant_user_assignment",
    "consume_auth_email_code",
    "create_auth_mfa_challenge_from_email_code",
    "deactivate_tenant_user_assignment",
    "enforce_auth_login_guard",
    "enqueue_notification_delivery",
    "find_auth_email_code_challenge",
    "issue_auth_email_code",
    "is_support_session",
    "is_tenant_support_session",
    "lookup_auth_user_by_id",
    "lookup_auth_sessions",
    "lookup_auth_session_mfa",
    "lookup_login_user_by_email",
    "mark_all_scoped_notifications_read",
    "mark_scoped_notification_read",
    "reactivate_tenant_user_assignment",
    "register_auth_session_device",
    "record_edge_writer_readiness",
    "record_auth_login_attempt",
    "record_role_permission_change",
    "reserve_sync_event_position",
    "resolve_notification_subscription",
    "revoke_auth_session_by_id",
    "revoke_auth_session_by_hash",
    "revoke_other_auth_sessions",
    "revoke_tenant_user_auth_sessions",
    "rotate_auth_session",
    "finalize_sync_event_position",
    "set_tenant_membership_status",
    "tenant_actor_has_scoped_permission",
    "tenant_actor_is_owner",
    "touch_auth_user_last_login",
    "update_tenant_membership_profile",
}

APP_EXECUTABLE_SECURITY_DEFINERS = sorted(
    APP_EXECUTABLE_FUNCTIONS - {"current_app_user_id", "current_tenant_id", "is_support_session"}
)

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
    'aurum_schema_owner',
    'aurum_migrator'
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
  AND owners.rolname = 'aurum_schema_owner'
ORDER BY routines.proname
"""

APP_EXECUTABLE_SECURITY_DEFINERS_SQL = """
SELECT DISTINCT routines.proname
FROM pg_catalog.pg_proc AS routines
JOIN pg_catalog.pg_namespace AS schemas
  ON schemas.oid = routines.pronamespace
WHERE schemas.nspname = 'public'
  AND routines.prosecdef
  AND pg_catalog.has_function_privilege('aurum_app', routines.oid, 'EXECUTE')
ORDER BY routines.proname
"""

TENANT_ACCOUNT_TABLE_PRIVILEGES_SQL = """
SELECT
  relations.relname,
  checks.privilege,
  pg_catalog.has_table_privilege(
    'aurum_support', relations.oid, checks.privilege
  ) AS support_has_privilege
FROM pg_catalog.pg_class AS relations
JOIN pg_catalog.pg_namespace AS schemas
  ON schemas.oid = relations.relnamespace
CROSS JOIN (
  VALUES
    ('SELECT'),
    ('INSERT'),
    ('UPDATE'),
    ('DELETE')
) AS checks(privilege)
WHERE schemas.nspname = 'public'
  AND relations.relname IN ('tenant_membership', 'tenant_ownership')
ORDER BY relations.relname, checks.privilege
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

APP_USER_COLUMN_PRIVILEGES_SQL = """
SELECT
  attributes.attname,
  pg_catalog.has_column_privilege(
    'aurum_app', 'public.app_user', attributes.attname, 'SELECT'
  ) AS can_select,
  pg_catalog.has_column_privilege(
    'aurum_app', 'public.app_user', attributes.attname, 'UPDATE'
  ) AS can_update
FROM pg_catalog.pg_attribute AS attributes
WHERE attributes.attrelid = 'public.app_user'::REGCLASS
  AND attributes.attnum > 0
  AND NOT attributes.attisdropped
ORDER BY attributes.attname
"""

SYNC_OUTBOX_COLUMN_PRIVILEGES_SQL = """
SELECT
  attributes.attname,
  pg_catalog.has_column_privilege(
    'aurum_app', 'public.sync_outbox', attributes.attname, 'INSERT'
  ) AS can_insert,
  pg_catalog.has_column_privilege(
    'aurum_app', 'public.sync_outbox', attributes.attname, 'UPDATE'
  ) AS can_update
FROM pg_catalog.pg_attribute AS attributes
WHERE attributes.attrelid = 'public.sync_outbox'::REGCLASS
  AND attributes.attnum > 0
  AND NOT attributes.attisdropped
ORDER BY attributes.attname
"""


def _assert_tenant_account_support_privileges(
    rows: list[RowMapping],
) -> None:
    actual = {
        "tenant_membership": set(),
        "tenant_ownership": set(),
    }
    for row in rows:
        if row["support_has_privilege"]:
            actual[str(row["relname"])].add(str(row["privilege"]))
    assert actual == {
        "tenant_membership": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "tenant_ownership": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    }


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
        app_user_column_result = await conn.execute(text(APP_USER_COLUMN_PRIVILEGES_SQL))
        app_user_column_privileges = list(app_user_column_result.mappings())
        sync_outbox_column_result = await conn.execute(text(SYNC_OUTBOX_COLUMN_PRIVILEGES_SQL))
        sync_outbox_column_privileges = list(sync_outbox_column_result.mappings())
        tenant_account_result = await conn.execute(text(TENANT_ACCOUNT_TABLE_PRIVILEGES_SQL))
        tenant_account_privileges = list(tenant_account_result.mappings())

    expected_relations = {
        **{table: {"SELECT", "INSERT", "UPDATE", "DELETE"} for table in CRUD_TABLES},
        **{table: {"SELECT", "INSERT"} for table in APPEND_ONLY_TABLES},
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
    assert all(row["owner"] == "aurum_schema_owner" for row in view_security)
    assert {
        row["attname"] for row in sync_outbox_column_privileges if row["can_insert"]
    } == SYNC_OUTBOX_INSERT_COLUMNS
    assert not any(row["can_update"] for row in sync_outbox_column_privileges)
    assert all("security_invoker=true" in row["options"] for row in view_security)
    assert {row["proname"] for row in function_privileges} == CUSTOM_FUNCTIONS
    assert {
        row["proname"] for row in function_privileges if row["app_can_execute"]
    } == APP_EXECUTABLE_FUNCTIONS
    assert all(row["is_grantable"] is False for row in function_privileges)
    assert all(row["public_can_execute"] is False for row in function_privileges)
    assert executable_definers == APP_EXECUTABLE_SECURITY_DEFINERS
    assert {
        (row["extname"], row["signature"]) for row in extension_privileges if row["app_can_execute"]
    } == APP_EXECUTABLE_EXTENSION_FUNCTIONS
    assert all(row["is_grantable"] is False for row in extension_privileges)
    assert sequence_privileges == []
    assert {
        row["attname"] for row in app_user_column_privileges if row["can_select"]
    } == APP_USER_SAFE_COLUMNS
    assert all(row["can_update"] is False for row in app_user_column_privileges)
    _assert_tenant_account_support_privileges(tenant_account_privileges)


async def test_support_cannot_delete_ownership_history_directly(
    support_engine_privileges: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    async with support_engine_privileges.begin() as conn:
        tenant_id = (
            await conn.execute(
                text(
                    "INSERT INTO public.tenant (name, contact_email) "
                    "VALUES (:name, :email) RETURNING id"
                ),
                {
                    "name": f"Ownership guard {suffix}",
                    "email": f"ownership-guard-{suffix}@example.invalid",
                },
            )
        ).scalar_one()
        user_id = (
            await conn.execute(
                text(
                    "INSERT INTO public.app_user (email, full_name, status) "
                    "VALUES (:email, 'Ownership guard', 'active') RETURNING id"
                ),
                {"email": f"ownership-guard-user-{suffix}@example.invalid"},
            )
        ).scalar_one()
        membership_id = (
            await conn.execute(
                text(
                    "INSERT INTO public.tenant_membership "
                    "(tenant_id, user_id, full_name, status) "
                    "VALUES (:tenant_id, :user_id, 'Ownership guard', 'active') "
                    "RETURNING id"
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
        ).scalar_one()
        ownership_id = (
            await conn.execute(
                text(
                    "INSERT INTO public.tenant_ownership "
                    "(tenant_id, membership_id) "
                    "VALUES (:tenant_id, :membership_id) RETURNING id"
                ),
                {"tenant_id": tenant_id, "membership_id": membership_id},
            )
        ).scalar_one()

    try:
        async with support_engine_privileges.begin() as conn:
            with pytest.raises(DBAPIError) as error:
                await conn.execute(
                    text("DELETE FROM public.tenant_ownership " "WHERE id = :ownership_id"),
                    {"ownership_id": ownership_id},
                )
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
    finally:
        async with support_engine_privileges.begin() as conn:
            await conn.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await conn.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": user_id},
            )


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
    for table in ("email_code", "login_attempt", "session"):
        await _assert_insufficient_privilege(
            app_engine_privileges,
            f"SELECT * FROM public.{table} LIMIT 1",
        )


async def _delete_auth_probe(
    support_engine: AsyncEngine,
    *,
    email: str,
) -> None:
    async with support_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM public.login_attempt WHERE email_lower = :email"),
            {"email": email},
        )
        await conn.execute(
            text("DELETE FROM public.email_code WHERE email_lower = :email"),
            {"email": email},
        )
        await conn.execute(
            text("DELETE FROM public.app_user WHERE email_lower = :email"),
            {"email": email},
        )


async def test_auth_functions_hide_secrets_and_reject_replay(
    support_engine_privileges: AsyncEngine,
    app_engine_privileges: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    email = f"auth-boundary-{suffix}@example.invalid"
    code = "246810"
    salt = uuid4().hex
    candidate_hash = hash_code(code, salt)
    first_refresh_hash = hash_token(f"first-{suffix}")
    second_refresh_hash = hash_token(f"second-{suffix}")
    rotation_operation_id = uuid4()
    password_hash = hash_password(f"password-{suffix}")

    try:
        async with support_engine_privileges.begin() as conn:
            user_id = (
                await conn.execute(
                    text(
                        "INSERT INTO public.app_user "
                        "(email, full_name, password_hash, status) "
                        "VALUES (:email, 'Auth boundary', :password_hash, 'active') "
                        "RETURNING id"
                    ),
                    {"email": email, "password_hash": password_hash},
                )
            ).scalar_one()

        async with app_engine_privileges.begin() as conn:
            issue_status = (
                await conn.execute(
                    text(
                        "SELECT public.issue_auth_email_code("
                        ":email, :candidate_hash, :salt, '127.0.0.1', NULL)"
                    ),
                    {
                        "email": email,
                        "candidate_hash": candidate_hash,
                        "salt": salt,
                    },
                )
            ).scalar_one()
            challenge = (
                (
                    await conn.execute(
                        text("SELECT * FROM public.find_auth_email_code_challenge(:email)"),
                        {"email": email},
                    )
                )
                .mappings()
                .one()
            )
            await conn.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": str(user_id)},
            )
            current_identity = (
                (
                    await conn.execute(
                        text(
                            "SELECT password_hash FROM public.lookup_auth_user_by_id("
                            ":user_id, NULL)"
                        ),
                        {"user_id": user_id},
                    )
                )
                .mappings()
                .one()
            )
            login_identity = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, password_hash "
                            "FROM public.lookup_login_user_by_email("
                            ":email, :code_id, :candidate_hash)"
                        ),
                        {
                            "email": email,
                            "code_id": challenge["id"],
                            "candidate_hash": candidate_hash,
                        },
                    )
                )
                .mappings()
                .one()
            )
            first_session_id = (
                await conn.execute(
                    text(
                        "SELECT public.create_auth_session_from_email_code("
                        ":code_id, :email, :candidate_hash, :refresh_hash, "
                        "NULL, '127.0.0.1', pg_catalog.now() + INTERVAL '7 days')"
                    ),
                    {
                        "code_id": challenge["id"],
                        "email": email,
                        "candidate_hash": candidate_hash,
                        "refresh_hash": first_refresh_hash,
                    },
                )
            ).scalar_one()
            replayed_session_id = (
                await conn.execute(
                    text(
                        "SELECT public.create_auth_session_from_email_code("
                        ":code_id, :email, :candidate_hash, :refresh_hash, "
                        "NULL, '127.0.0.1', pg_catalog.now() + INTERVAL '7 days')"
                    ),
                    {
                        "code_id": challenge["id"],
                        "email": email,
                        "candidate_hash": candidate_hash,
                        "refresh_hash": second_refresh_hash,
                    },
                )
            ).scalar_one()
            rotated = (
                (
                    await conn.execute(
                        text(
                            "SELECT * FROM public.rotate_auth_session("
                            ":old_hash, :new_hash, :operation_id, NULL, '127.0.0.1', "
                            "pg_catalog.now() + INTERVAL '7 days')"
                        ),
                        {
                            "old_hash": first_refresh_hash,
                            "new_hash": second_refresh_hash,
                            "operation_id": rotation_operation_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            replayed_rotation = (
                (
                    await conn.execute(
                        text(
                            "SELECT * FROM public.rotate_auth_session("
                            ":old_hash, :new_hash, :operation_id, NULL, '127.0.0.1', "
                            "pg_catalog.now() + INTERVAL '7 days')"
                        ),
                        {
                            "old_hash": first_refresh_hash,
                            "new_hash": hash_token(f"third-{suffix}"),
                            "operation_id": rotation_operation_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            revoked_user_id = (
                await conn.execute(
                    text(
                        "SELECT public.revoke_auth_session_by_hash(" ":token_hash, 'logout', NULL)"
                    ),
                    {"token_hash": second_refresh_hash},
                )
            ).scalar_one()
            replayed_revoke = (
                await conn.execute(
                    text(
                        "SELECT public.revoke_auth_session_by_hash(" ":token_hash, 'logout', NULL)"
                    ),
                    {"token_hash": second_refresh_hash},
                )
            ).scalar_one()

        assert issue_status == "created"
        assert challenge["code_salt"] == salt
        assert current_identity["password_hash"] is None
        assert login_identity == {"id": user_id, "password_hash": password_hash}
        assert first_session_id is not None
        assert replayed_session_id is None
        assert rotated["user_id"] == user_id
        assert replayed_rotation is None
        assert revoked_user_id == user_id
        assert replayed_revoke is None
    finally:
        await _delete_auth_probe(support_engine_privileges, email=email)


async def test_auth_code_and_refresh_are_single_use_under_concurrency(
    support_engine_privileges: AsyncEngine,
    app_engine_privileges: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    email = f"auth-race-{suffix}@example.invalid"
    salt = uuid4().hex
    candidate_hash = hash_code("135790", salt)
    refresh_hashes = [hash_token(f"login-{suffix}-{index}") for index in range(2)]

    async def create_session(refresh_hash: str) -> str | None:
        async with app_engine_privileges.begin() as conn:
            value = (
                await conn.execute(
                    text(
                        "SELECT public.create_auth_session_from_email_code("
                        ":code_id, :email, :candidate_hash, :refresh_hash, "
                        "NULL, '127.0.0.1', pg_catalog.now() + INTERVAL '7 days')"
                    ),
                    {
                        "code_id": code_id,
                        "email": email,
                        "candidate_hash": candidate_hash,
                        "refresh_hash": refresh_hash,
                    },
                )
            ).scalar_one()
            return str(value) if value is not None else None

    async def rotate_session(old_hash: str, new_hash: str, operation_id: UUID) -> str | None:
        async with app_engine_privileges.begin() as conn:
            value = (
                await conn.execute(
                    text(
                        "SELECT id FROM public.rotate_auth_session("
                        ":old_hash, :new_hash, :operation_id, NULL, '127.0.0.1', "
                        "pg_catalog.now() + INTERVAL '7 days')"
                    ),
                    {
                        "old_hash": old_hash,
                        "new_hash": new_hash,
                        "operation_id": operation_id,
                    },
                )
            ).scalar_one_or_none()
            return str(value) if value is not None else None

    try:
        async with support_engine_privileges.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.app_user (email, full_name, status) "
                    "VALUES (:email, 'Auth race', 'active')"
                ),
                {"email": email},
            )

        async with app_engine_privileges.begin() as conn:
            await conn.execute(
                text(
                    "SELECT public.issue_auth_email_code("
                    ":email, :candidate_hash, :salt, '127.0.0.1', NULL)"
                ),
                {
                    "email": email,
                    "candidate_hash": candidate_hash,
                    "salt": salt,
                },
            )
            code_id = (
                await conn.execute(
                    text("SELECT id FROM public.find_auth_email_code_challenge(:email)"),
                    {"email": email},
                )
            ).scalar_one()

        created = await asyncio.gather(*(create_session(value) for value in refresh_hashes))
        assert sum(value is not None for value in created) == 1
        winning_index = next(index for index, value in enumerate(created) if value is not None)
        winning_hash = refresh_hashes[winning_index]

        rotated_hashes = [hash_token(f"rotate-{suffix}-{index}") for index in range(2)]
        rotation_ids = [uuid4(), uuid4()]
        rotated = await asyncio.gather(
            *(
                rotate_session(winning_hash, token_hash, operation_id)
                for token_hash, operation_id in zip(rotated_hashes, rotation_ids, strict=True)
            )
        )
        assert sum(value is not None for value in rotated) == 1
        rotation_winner = next(index for index, value in enumerate(rotated) if value is not None)

        retried = await asyncio.gather(
            *(
                rotate_session(
                    winning_hash,
                    rotated_hashes[rotation_winner],
                    rotation_ids[rotation_winner],
                )
                for _ in range(2)
            )
        )
        assert retried == [rotated[rotation_winner], rotated[rotation_winner]]
    finally:
        await _delete_auth_probe(support_engine_privileges, email=email)


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


@pytest.mark.parametrize(
    "ddl",
    (
        "CREATE TABLE public.support_must_not_create_table (id INTEGER)",
        "CREATE SEQUENCE public.support_must_not_create_sequence",
        (
            "CREATE FUNCTION public.support_must_not_create_function() "
            "RETURNS INTEGER LANGUAGE SQL AS 'SELECT 1'"
        ),
    ),
)
async def test_support_cannot_create_database_objects(
    support_engine_privileges: AsyncEngine,
    ddl: str,
) -> None:
    with pytest.raises(DBAPIError):
        async with support_engine_privileges.begin() as conn:
            await conn.execute(text(ddl))


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
