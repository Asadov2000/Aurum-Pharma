"""security: deny runtime access to new database objects by default

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-13

Existing databases need this revision to run once as the database owner so
database-level ACLs and that owner's default privileges can be hardened. Fresh
databases are already prepared by infra/postgres/init.sh and may run the normal
support-role Alembic flow.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CRUD_TABLES = (
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
    "notification_delivery",
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
)

READ_ONLY_TABLES = (
    "audit_log",
    "master_catalog",
    "permission",
    "role_template",
    "role_template_permission",
    "subscription_plan",
)

NO_ACCESS_TABLES = ("alembic_version",)

RUNTIME_VIEWS = (
    "v_active_subscription",
    "v_batch_with_expiry_status",
)

CUSTOM_FUNCTIONS = (
    "public.append_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB)",
    "public.audit_redact_jsonb(JSONB)",
    "public.current_app_user_id()",
    "public.current_tenant_id()",
    "public.is_support_session()",
    "public.trg_audit_log()",
    "public.trg_set_created_meta()",
    "public.trg_set_updated_meta()",
    "public.trg_update_batch_qty()",
)

APP_EXECUTABLE_FUNCTIONS = (
    "public.append_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB)",
    "public.current_app_user_id()",
    "public.current_tenant_id()",
    "public.is_support_session()",
)


OWNER_HARDENING_SQL = """
DO $$
DECLARE
  v_database_owner TEXT;
BEGIN
  SELECT pg_catalog.pg_get_userbyid(databases.datdba)
  INTO v_database_owner
  FROM pg_catalog.pg_database AS databases
  WHERE databases.datname = current_database();

  IF session_user = v_database_owner THEN
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC',
      current_database()
    );
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON DATABASE %I FROM aurum_app',
      current_database()
    );
    EXECUTE pg_catalog.format(
      'GRANT CONNECT ON DATABASE %I TO aurum_app',
      current_database()
    );

    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
      'REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, aurum_app',
      v_database_owner
    );
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
      'REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, aurum_app',
      v_database_owner
    );
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
      'REVOKE ALL PRIVILEGES ON FUNCTIONS FROM aurum_app',
      v_database_owner
    );
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
      'REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC',
      v_database_owner
    );
  END IF;
END
$$
"""


SUPPORT_DEFAULT_PRIVILEGES_SQL = (
    "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
    "REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, aurum_app",
    "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
    "REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, aurum_app",
    "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
    "REVOKE ALL PRIVILEGES ON FUNCTIONS FROM aurum_app",
    "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
    "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
)


SECURITY_POSTURE_GUARD_SQL = """
DO $$
DECLARE
  v_database_owner TEXT;
  v_bad_default_owner TEXT;
  v_bad_default_type "char";
  v_bad_default_privilege TEXT;
  v_bad_function TEXT;
BEGIN
  SELECT pg_catalog.pg_get_userbyid(databases.datdba)
  INTO v_database_owner
  FROM pg_catalog.pg_database AS databases
  WHERE databases.datname = current_database();

  IF NOT pg_catalog.has_database_privilege(
    'aurum_app', current_database(), 'CONNECT'
  ) OR pg_catalog.has_database_privilege(
    'aurum_app', current_database(), 'CREATE'
  ) OR pg_catalog.has_database_privilege(
    'aurum_app', current_database(), 'TEMP'
  ) THEN
    RAISE EXCEPTION
      'Database-owner hardening is required before revision 0030; '
      'run the owner migration command';
  END IF;

  IF EXISTS (
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
  ) THEN
    RAISE EXCEPTION
      'PUBLIC must not have privileges on the application database';
  END IF;

  SELECT owners.rolname, defaults.defaclobjtype, acl.privilege_type
  INTO v_bad_default_owner, v_bad_default_type, v_bad_default_privilege
  FROM pg_catalog.pg_default_acl AS defaults
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = defaults.defaclrole
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = defaults.defaclnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS acl
  LEFT JOIN pg_catalog.pg_roles AS grantees
    ON grantees.oid = acl.grantee
  WHERE schemas.nspname = 'public'
    AND owners.rolname IN (v_database_owner, 'aurum_support')
    AND (
      grantees.rolname = 'aurum_app'
      OR (
        defaults.defaclobjtype = 'f'
        AND acl.grantee = 0
        AND acl.privilege_type = 'EXECUTE'
      )
    )
  LIMIT 1;

  IF v_bad_default_owner IS NOT NULL THEN
    RAISE EXCEPTION
      'Unsafe default privilege remains for owner %, object type %, privilege %; '
      'run the owner migration command',
      v_bad_default_owner,
      v_bad_default_type,
      v_bad_default_privilege;
  END IF;

  SELECT
    routines.proname || '('
      || pg_catalog.pg_get_function_identity_arguments(routines.oid) || ')'
  INTO v_bad_function
  FROM pg_catalog.pg_proc AS routines
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = routines.pronamespace
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = routines.proowner
  WHERE schemas.nspname = 'public'
    AND owners.rolname = 'aurum_support'
    AND pg_catalog.has_function_privilege('aurum_app', routines.oid, 'EXECUTE')
    AND routines.oid <> ALL(ARRAY[
      'public.append_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB)'::regprocedure,
      'public.current_app_user_id()'::regprocedure,
      'public.current_tenant_id()'::regprocedure,
      'public.is_support_session()'::regprocedure
    ])
  LIMIT 1;

  IF v_bad_function IS NOT NULL THEN
    RAISE EXCEPTION 'aurum_app may execute unexpected function %', v_bad_function;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_catalog.pg_proc AS routines
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = routines.pronamespace
    WHERE schemas.nspname = 'public'
      AND routines.prosecdef
      AND pg_catalog.has_function_privilege('aurum_app', routines.oid, 'EXECUTE')
      AND routines.oid <>
        'public.append_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB)'::regprocedure
  ) THEN
    RAISE EXCEPTION 'aurum_app may execute an unexpected SECURITY DEFINER function';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_catalog.unnest(ARRAY[
      'public.append_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB)'::regprocedure,
      'public.current_app_user_id()'::regprocedure,
      'public.current_tenant_id()'::regprocedure,
      'public.is_support_session()'::regprocedure
    ]) AS allowed_functions(function_oid)
    WHERE NOT pg_catalog.has_function_privilege(
      'aurum_app', allowed_functions.function_oid, 'EXECUTE'
    ) OR pg_catalog.has_function_privilege(
      'aurum_app',
      allowed_functions.function_oid,
      'EXECUTE WITH GRANT OPTION'
    )
  ) THEN
    RAISE EXCEPTION 'aurum_app function allowlist is incomplete or grantable';
  END IF;
END
$$
"""


def _sql_array(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _relation_guard_sql() -> str:
    expected_cte = f"""
    expected(relname, privileges) AS (
      SELECT names.relname, ARRAY['SELECT','INSERT','UPDATE','DELETE']::TEXT[]
      FROM pg_catalog.unnest(ARRAY[{_sql_array(CRUD_TABLES)}]) AS names(relname)
      UNION ALL
      SELECT names.relname, ARRAY['SELECT']::TEXT[]
      FROM pg_catalog.unnest(
        ARRAY[{_sql_array(READ_ONLY_TABLES + RUNTIME_VIEWS)}]
      ) AS names(relname)
      UNION ALL
      SELECT names.relname, ARRAY[]::TEXT[]
      FROM pg_catalog.unnest(ARRAY[{_sql_array(NO_ACCESS_TABLES)}]) AS names(relname)
    )
    """
    return f"""
    DO $$
    DECLARE
      v_relation TEXT;
      v_privilege TEXT;
    BEGIN
      WITH {expected_cte}
      SELECT relations.relname
      INTO v_relation
      FROM pg_catalog.pg_class AS relations
      JOIN pg_catalog.pg_namespace AS schemas
        ON schemas.oid = relations.relnamespace
      LEFT JOIN expected ON expected.relname = relations.relname
      WHERE schemas.nspname = 'public'
        AND relations.relkind IN ('r', 'p', 'v', 'm', 'f')
        AND expected.relname IS NULL
      LIMIT 1;

      IF v_relation IS NOT NULL THEN
        RAISE EXCEPTION 'Unclassified public relation %', v_relation;
      END IF;

      WITH {expected_cte}
      SELECT expected.relname
      INTO v_relation
      FROM expected
      LEFT JOIN pg_catalog.pg_class AS relations
        ON relations.relname = expected.relname
       AND relations.relnamespace = 'public'::regnamespace
       AND relations.relkind IN ('r', 'p', 'v', 'm', 'f')
      WHERE relations.oid IS NULL
      LIMIT 1;

      IF v_relation IS NOT NULL THEN
        RAISE EXCEPTION 'Expected public relation % is missing', v_relation;
      END IF;

      WITH {expected_cte}
      SELECT expected.relname, checks.privilege
      INTO v_relation, v_privilege
      FROM expected
      JOIN pg_catalog.pg_class AS relations
        ON relations.relname = expected.relname
       AND relations.relnamespace = 'public'::regnamespace
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
      WHERE pg_catalog.has_table_privilege(
        'aurum_app', relations.oid, checks.privilege
      ) IS DISTINCT FROM (checks.privilege = ANY(expected.privileges))
        OR pg_catalog.has_table_privilege(
          'aurum_app',
          relations.oid,
          checks.privilege || ' WITH GRANT OPTION'
        )
      LIMIT 1;

      IF v_relation IS NOT NULL THEN
        RAISE EXCEPTION
          'Unexpected effective privilege % on public.%',
          v_privilege,
          v_relation;
      END IF;
    END
    $$
    """


def _grant_tables(privileges: str, tables: tuple[str, ...]) -> None:
    table_list = ", ".join(f"public.{table}" for table in tables)
    op.execute(f"GRANT {privileges} ON TABLE {table_list} TO aurum_app")


def upgrade() -> None:
    op.execute(OWNER_HARDENING_SQL)
    for statement in SUPPORT_DEFAULT_PRIVILEGES_SQL:
        op.execute(statement)

    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public " "FROM PUBLIC, aurum_app")
    _grant_tables("SELECT, INSERT, UPDATE, DELETE", CRUD_TABLES)
    _grant_tables("SELECT", READ_ONLY_TABLES)

    for view in RUNTIME_VIEWS:
        op.execute(f"ALTER VIEW public.{view} SET (security_invoker = true)")
    _grant_tables("SELECT", RUNTIME_VIEWS)

    for function in CUSTOM_FUNCTIONS:
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC, aurum_app")
    for function in APP_EXECUTABLE_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_app")

    op.execute(_relation_guard_sql())
    op.execute(SECURITY_POSTURE_GUARD_SQL)


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aurum_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
        "GRANT ALL PRIVILEGES ON SEQUENCES TO aurum_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
        "GRANT ALL PRIVILEGES ON FUNCTIONS TO aurum_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
        "GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE " "ON ALL TABLES IN SCHEMA public TO aurum_app"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.audit_log FROM aurum_app")
    op.execute("GRANT SELECT ON TABLE public.audit_log TO aurum_app")
    for view in RUNTIME_VIEWS:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{view} FROM aurum_app")
        op.execute(f"GRANT SELECT ON TABLE public.{view} TO aurum_app")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM aurum_app")

    legacy_public_functions = (
        "public.current_app_user_id()",
        "public.current_tenant_id()",
        "public.is_support_session()",
        "public.trg_set_created_meta()",
        "public.trg_set_updated_meta()",
        "public.trg_update_batch_qty()",
    )
    for function in legacy_public_functions:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC, aurum_app")

    op.execute("REVOKE ALL PRIVILEGES ON FUNCTION public.trg_audit_log() " "FROM PUBLIC, aurum_app")
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION public.audit_redact_jsonb(JSONB) "
        "FROM PUBLIC, aurum_app"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.append_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "public.append_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB) "
        "TO aurum_app, aurum_support"
    )
