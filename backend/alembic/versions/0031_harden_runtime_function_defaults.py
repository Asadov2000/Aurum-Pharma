"""security: remove implicit execution rights from future functions

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-13

PostgreSQL grants EXECUTE on new functions to PUBLIC unless the object owner's
global default privileges override that built-in default. Schema-scoped
ALTER DEFAULT PRIVILEGES cannot revoke a global default.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OWNER_GLOBAL_DEFAULTS_SQL = """
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
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
      'REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, aurum_app',
      v_database_owner
    );
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
      'REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, aurum_app',
      v_database_owner
    );
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
      'REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC, aurum_app',
      v_database_owner
    );
  END IF;
END
$$
"""


SUPPORT_GLOBAL_DEFAULTS_SQL = (
    "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support "
    "REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC, aurum_app",
    "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support "
    "REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC, aurum_app",
    "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support "
    "REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC, aurum_app",
)


DEFAULT_PRIVILEGES_GUARD_SQL = """
DO $$
DECLARE
  v_unsafe_owner TEXT;
  v_unsafe_scope TEXT;
  v_unsafe_type "char";
  v_unsafe_grantee TEXT;
  v_unsafe_privilege TEXT;
BEGIN
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
      '<global>'::TEXT AS scope,
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
      schemas.nspname AS scope,
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
  SELECT owner, scope, object_type, grantee, privilege_type
  INTO
    v_unsafe_owner,
    v_unsafe_scope,
    v_unsafe_type,
    v_unsafe_grantee,
    v_unsafe_privilege
  FROM unsafe_defaults
  LIMIT 1;

  IF v_unsafe_owner IS NOT NULL THEN
    RAISE EXCEPTION
      'Unsafe default privilege remains: owner %, scope %, type %, grantee %, privilege %; '
      'run the owner migration command',
      v_unsafe_owner,
      v_unsafe_scope,
      v_unsafe_type,
      v_unsafe_grantee,
      v_unsafe_privilege;
  END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(OWNER_GLOBAL_DEFAULTS_SQL)
    for statement in SUPPORT_GLOBAL_DEFAULTS_SQL:
        op.execute(statement)
    op.execute(DEFAULT_PRIVILEGES_GUARD_SQL)


def downgrade() -> None:
    # Owner-level defaults remain hardened deliberately. Restoring them would
    # require elevated credentials and would reintroduce an unsafe default.
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support " "GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
    )
