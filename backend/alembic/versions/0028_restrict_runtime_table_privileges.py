"""security: restrict runtime table privileges to row-level DML

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RUNTIME_TABLE_PRIVILEGE_GUARD_SQL = """
DO $$
DECLARE
  v_unsafe_table TEXT;
  v_unsafe_privilege TEXT;
  v_default_privileges TEXT[];
BEGIN
  SELECT relations.relname, privileges.privilege
  INTO v_unsafe_table, v_unsafe_privilege
  FROM pg_catalog.pg_class AS relations
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = relations.relnamespace
  CROSS JOIN (
    VALUES ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
  ) AS privileges(privilege)
  WHERE schemas.nspname = 'public'
    AND relations.relkind IN ('r', 'p', 'v', 'm', 'f')
    AND pg_catalog.has_table_privilege(
      'aurum_app',
      pg_catalog.format('%I.%I', schemas.nspname, relations.relname),
      privileges.privilege
    )
  LIMIT 1;

  IF v_unsafe_table IS NOT NULL THEN
    RAISE EXCEPTION
      'aurum_app still has % on public.%',
      v_unsafe_privilege,
      v_unsafe_table;
  END IF;

  SELECT pg_catalog.array_agg(
    acl.privilege_type::TEXT ORDER BY acl.privilege_type::TEXT
  )
  INTO v_default_privileges
  FROM pg_catalog.pg_default_acl AS defaults
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = defaults.defaclrole
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = defaults.defaclnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS acl
  JOIN pg_catalog.pg_roles AS grantees
    ON grantees.oid = acl.grantee
  WHERE owners.rolname = 'aurum_support'
    AND schemas.nspname = 'public'
    AND defaults.defaclobjtype = 'r'
    AND grantees.rolname = 'aurum_app';

  IF v_default_privileges IS DISTINCT FROM
    ARRAY['DELETE', 'INSERT', 'SELECT', 'UPDATE']::TEXT[]
  THEN
    RAISE EXCEPTION
      'aurum_app table defaults are not limited to row-level DML: %',
      v_default_privileges;
  END IF;
END
$$
"""


def upgrade() -> None:
    # RLS and row-level audit triggers do not protect TRUNCATE. The runtime
    # role also has no reason to define foreign keys or attach triggers.
    op.execute(
        "REVOKE TRUNCATE, REFERENCES, TRIGGER " "ON ALL TABLES IN SCHEMA public FROM aurum_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON TABLES FROM aurum_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aurum_app"
    )
    op.execute(RUNTIME_TABLE_PRIVILEGE_GUARD_SQL)


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON TABLES FROM aurum_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE aurum_support IN SCHEMA public "
        "GRANT ALL PRIVILEGES ON TABLES TO aurum_app"
    )
    op.execute("GRANT TRUNCATE, REFERENCES, TRIGGER " "ON ALL TABLES IN SCHEMA public TO aurum_app")
    # Migration 0026 already limited the immutable ledger to SELECT.
    op.execute("REVOKE TRUNCATE, REFERENCES, TRIGGER " "ON TABLE public.audit_log FROM aurum_app")
