"""security: allow only required trusted extension functions at runtime

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-13

The deny-by-default function policy also covers functions installed by trusted
PostgreSQL extensions. Runtime search needs pg_trgm's percent operator, and
table UUID defaults need pgcrypto's gen_random_uuid function.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RUNTIME_EXTENSION_FUNCTIONS = (
    "public.similarity_op(TEXT, TEXT)",
    "public.gen_random_uuid()",
)


OWNER_EXTENSION_PRIVILEGES_SQL = """
DO $$
DECLARE
  v_database_owner TEXT;
  v_function REGPROCEDURE;
BEGIN
  SELECT pg_catalog.pg_get_userbyid(databases.datdba)
  INTO v_database_owner
  FROM pg_catalog.pg_database AS databases
  WHERE databases.datname = current_database();

  IF session_user = v_database_owner THEN
    FOR v_function IN
      SELECT routines.oid::REGPROCEDURE
      FROM pg_catalog.pg_proc AS routines
      JOIN pg_catalog.pg_depend AS dependencies
        ON dependencies.classid = 'pg_proc'::REGCLASS
       AND dependencies.objid = routines.oid
       AND dependencies.deptype = 'e'
      JOIN pg_catalog.pg_extension AS extensions
        ON extensions.oid = dependencies.refobjid
      WHERE extensions.extname IN ('pg_trgm', 'pgcrypto', 'unaccent')
    LOOP
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM PUBLIC, aurum_app',
        v_function
      );
    END LOOP;

    GRANT EXECUTE ON FUNCTION public.similarity_op(TEXT, TEXT)
      TO aurum_app, aurum_support;
    GRANT EXECUTE ON FUNCTION public.gen_random_uuid()
      TO aurum_app, aurum_support;
  END IF;
END
$$
"""


EXTENSION_PRIVILEGES_GUARD_SQL = """
DO $$
DECLARE
  v_bad_extension TEXT;
  v_bad_function TEXT;
  v_expected_extension TEXT;
  v_expected_function REGPROCEDURE;
  v_actual_extension TEXT;
BEGIN
  SELECT
    extensions.extname,
    pg_catalog.format(
      '%I.%I(%s)',
      schemas.nspname,
      routines.proname,
      pg_catalog.pg_get_function_identity_arguments(routines.oid)
    )
  INTO v_bad_extension, v_bad_function
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
    AND pg_catalog.has_function_privilege('aurum_app', routines.oid, 'EXECUTE')
    AND routines.oid <> ALL(ARRAY[
      'public.similarity_op(TEXT, TEXT)'::REGPROCEDURE,
      'public.gen_random_uuid()'::REGPROCEDURE
    ])
  LIMIT 1;

  IF v_bad_function IS NOT NULL THEN
    RAISE EXCEPTION
      'Extension % exposes unexpected runtime function %',
      v_bad_extension,
      v_bad_function;
  END IF;

  FOR v_expected_extension, v_expected_function IN
    SELECT expected.extension_name, expected.function_name::REGPROCEDURE
    FROM (
      VALUES
        ('pg_trgm', 'public.similarity_op(TEXT, TEXT)'),
        ('pgcrypto', 'public.gen_random_uuid()')
    ) AS expected(extension_name, function_name)
  LOOP
    SELECT extensions.extname
    INTO v_actual_extension
    FROM pg_catalog.pg_depend AS dependencies
    JOIN pg_catalog.pg_extension AS extensions
      ON extensions.oid = dependencies.refobjid
    WHERE dependencies.classid = 'pg_proc'::REGCLASS
      AND dependencies.objid = v_expected_function
      AND dependencies.deptype = 'e';

    IF v_actual_extension IS DISTINCT FROM v_expected_extension THEN
      RAISE EXCEPTION
        'Expected function % is not owned by extension %',
        v_expected_function,
        v_expected_extension;
    END IF;

    IF NOT pg_catalog.has_function_privilege(
      'aurum_app', v_expected_function, 'EXECUTE'
    ) OR pg_catalog.has_function_privilege(
      'aurum_app', v_expected_function, 'EXECUTE WITH GRANT OPTION'
    ) THEN
      RAISE EXCEPTION
        'Runtime extension function % is unavailable or grantable; '
        'run the owner migration command',
        v_expected_function;
    END IF;
  END LOOP;
END
$$
"""


def upgrade() -> None:
    op.execute(OWNER_EXTENSION_PRIVILEGES_SQL)
    op.execute(EXTENSION_PRIVILEGES_GUARD_SQL)


def downgrade() -> None:
    for function in RUNTIME_EXTENSION_FUNCTIONS:
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM aurum_app")
