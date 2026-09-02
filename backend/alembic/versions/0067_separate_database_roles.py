"""security: separate runtime support from database object ownership

Revision ID: 0067
Revises: 0066
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0067"
down_revision: str | Sequence[str] | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREFLIGHT_SQL = """
DO $$
DECLARE
  v_bad_object TEXT;
BEGIN
  IF session_user <> 'aurum_migrator' OR current_user <> 'aurum_migrator' THEN
    RAISE EXCEPTION 'revision 0067 must run directly as aurum_migrator';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = 'aurum_app'
      AND rolcanlogin
      AND NOT rolsuper
      AND NOT rolcreatedb
      AND NOT rolcreaterole
      AND NOT rolreplication
      AND NOT rolbypassrls
  ) THEN
    RAISE EXCEPTION 'aurum_app role attributes violate the database role contract';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = 'aurum_support'
      AND rolcanlogin
      AND NOT rolsuper
      AND NOT rolcreatedb
      AND NOT rolcreaterole
      AND NOT rolreplication
      AND rolbypassrls
  ) THEN
    RAISE EXCEPTION 'aurum_support role attributes violate the database role contract';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = 'aurum_schema_owner'
      AND NOT rolcanlogin
      AND NOT rolsuper
      AND NOT rolcreatedb
      AND NOT rolcreaterole
      AND NOT rolreplication
      AND rolbypassrls
  ) THEN
    RAISE EXCEPTION 'aurum_schema_owner role attributes violate the database role contract';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = 'aurum_migrator'
      AND rolcanlogin
      AND rolinherit
      AND NOT rolsuper
      AND NOT rolcreatedb
      AND NOT rolcreaterole
      AND NOT rolreplication
      AND NOT rolbypassrls
  ) THEN
    RAISE EXCEPTION 'aurum_migrator role attributes violate the database role contract';
  END IF;

  IF NOT pg_catalog.pg_has_role(
    'aurum_migrator', 'aurum_schema_owner', 'MEMBER'
  ) OR NOT pg_catalog.pg_has_role(
    'aurum_migrator', 'aurum_support', 'MEMBER'
  ) THEN
    RAISE EXCEPTION 'aurum_migrator must be a member of owner and legacy support roles';
  END IF;

  IF pg_catalog.pg_has_role(
    'aurum_app', 'aurum_schema_owner', 'MEMBER'
  ) OR pg_catalog.pg_has_role(
    'aurum_app', 'aurum_migrator', 'MEMBER'
  ) OR pg_catalog.pg_has_role(
    'aurum_support', 'aurum_schema_owner', 'MEMBER'
  ) OR pg_catalog.pg_has_role(
    'aurum_support', 'aurum_migrator', 'MEMBER'
  ) THEN
    RAISE EXCEPTION 'runtime roles must not inherit migration or owner roles';
  END IF;

  IF (
    SELECT pg_catalog.pg_get_userbyid(datdba)
    FROM pg_catalog.pg_database
    WHERE datname = current_database()
  ) IS DISTINCT FROM 'aurum_schema_owner' THEN
    RAISE EXCEPTION 'bootstrap must assign the application database to aurum_schema_owner';
  END IF;

  SELECT shared_object
  INTO v_bad_object
  FROM (
    SELECT 'database:' || pg_catalog.quote_ident(databases.datname) AS shared_object
    FROM pg_catalog.pg_database AS databases
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = databases.datdba
    WHERE owners.rolname = 'aurum_support'

    UNION ALL

    SELECT 'tablespace:' || pg_catalog.quote_ident(tablespaces.spcname)
    FROM pg_catalog.pg_tablespace AS tablespaces
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = tablespaces.spcowner
    WHERE owners.rolname = 'aurum_support'
  ) AS shared_objects
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION
      'refusing cluster-wide ownership transfer; aurum_support owns %',
      v_bad_object;
  END IF;

  IF (
    SELECT pg_catalog.pg_get_userbyid(nspowner)
    FROM pg_catalog.pg_namespace
    WHERE nspname = 'public'
  ) IS DISTINCT FROM 'aurum_support' THEN
    RAISE EXCEPTION 'public schema must have the legacy aurum_support owner before 0067';
  END IF;

  SELECT pg_catalog.format('%I.%I', schemas.nspname, relations.relname)
  INTO v_bad_object
  FROM pg_catalog.pg_class AS relations
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = relations.relnamespace
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = relations.relowner
  WHERE schemas.nspname = 'public'
    AND relations.relpersistence <> 't'
    AND owners.rolname <> 'aurum_support'
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected owner before role separation for %', v_bad_object;
  END IF;

  SELECT pg_catalog.format(
    '%I.%I(%s)',
    schemas.nspname,
    routines.proname,
    pg_catalog.pg_get_function_identity_arguments(routines.oid)
  )
  INTO v_bad_object
  FROM pg_catalog.pg_proc AS routines
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = routines.pronamespace
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = routines.proowner
  WHERE schemas.nspname = 'public'
    AND NOT EXISTS (
      SELECT 1
      FROM pg_catalog.pg_depend AS dependencies
      WHERE dependencies.classid = 'pg_proc'::REGCLASS
        AND dependencies.objid = routines.oid
        AND dependencies.deptype = 'e'
    )
    AND owners.rolname <> 'aurum_support'
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected function owner before role separation for %', v_bad_object;
  END IF;

  SELECT extensions.extname
  INTO v_bad_object
  FROM pg_catalog.pg_extension AS extensions
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = extensions.extowner
  WHERE extensions.extname IN ('pgcrypto', 'pg_trgm', 'unaccent')
    AND owners.rolname <> 'aurum_support'
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected extension owner before role separation for %', v_bad_object;
  END IF;

  SELECT routines.oid::REGPROCEDURE::TEXT
  INTO v_bad_object
  FROM pg_catalog.pg_proc AS routines
  JOIN pg_catalog.pg_depend AS dependencies
    ON dependencies.classid = 'pg_proc'::REGCLASS
   AND dependencies.objid = routines.oid
   AND dependencies.deptype = 'e'
  JOIN pg_catalog.pg_extension AS extensions
    ON extensions.oid = dependencies.refobjid
  WHERE extensions.extname IN ('pgcrypto', 'pg_trgm', 'unaccent')
    AND pg_catalog.has_function_privilege(
      'aurum_support', routines.oid, 'EXECUTE'
    )
    AND routines.oid <> ALL(ARRAY[
      'public.similarity_op(TEXT, TEXT)'::REGPROCEDURE,
      'public.gen_random_uuid()'::REGPROCEDURE,
      'public.pgp_sym_encrypt(TEXT, TEXT)'::REGPROCEDURE,
      'public.pgp_sym_encrypt(TEXT, TEXT, TEXT)'::REGPROCEDURE,
      'public.pgp_sym_decrypt(BYTEA, TEXT)'::REGPROCEDURE,
      'public.pgp_sym_decrypt(BYTEA, TEXT, TEXT)'::REGPROCEDURE
    ])
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'aurum_support can execute unexpected extension function %', v_bad_object;
  END IF;
END
$$
"""


TRANSFER_AND_GRANTS_SQL = (
    "GRANT USAGE, CREATE ON SCHEMA public TO aurum_schema_owner",
    "REASSIGN OWNED BY aurum_support TO aurum_schema_owner",
    """
DO $$
BEGIN
  EXECUTE pg_catalog.format(
    'REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC, aurum_app, '
    'aurum_support, aurum_migrator',
    current_database()
  );
  EXECUTE pg_catalog.format(
    'GRANT CONNECT ON DATABASE %I TO aurum_app, aurum_support, aurum_migrator',
    current_database()
  );
END
$$
""",
    """
REVOKE ALL PRIVILEGES ON SCHEMA public
  FROM PUBLIC, aurum_app, aurum_support, aurum_migrator, aurum_schema_owner
""",
    "GRANT ALL PRIVILEGES ON SCHEMA public TO aurum_schema_owner",
    "GRANT USAGE ON SCHEMA public TO aurum_app, aurum_support",
    """
DO $$
DECLARE
  v_relation REGCLASS;
  v_name TEXT;
  v_kind "char";
BEGIN
  FOR v_relation, v_name, v_kind IN
    SELECT relations.oid::REGCLASS, relations.relname, relations.relkind
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = relations.relnamespace
    WHERE schemas.nspname = 'public'
      AND relations.relkind IN ('r', 'p', 'v', 'm')
  LOOP
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON TABLE %s FROM aurum_support',
      v_relation
    );
    IF v_name = 'alembic_version' THEN
      CONTINUE;
    ELSIF v_name = 'audit_log' OR v_kind IN ('v', 'm') THEN
      EXECUTE pg_catalog.format(
        'GRANT SELECT ON TABLE %s TO aurum_support',
        v_relation
      );
    ELSE
      EXECUTE pg_catalog.format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %s TO aurum_support',
        v_relation
      );
    END IF;
  END LOOP;
END
$$
""",
    """
DO $$
DECLARE
  v_sequence REGCLASS;
BEGIN
  FOR v_sequence IN
    SELECT relations.oid::REGCLASS
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = relations.relnamespace
    WHERE schemas.nspname = 'public'
      AND relations.relkind = 'S'
  LOOP
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM aurum_support',
      v_sequence
    );
    EXECUTE pg_catalog.format(
      'GRANT USAGE, SELECT, UPDATE ON SEQUENCE %s TO aurum_support',
      v_sequence
    );
  END LOOP;
END
$$
""",
    """
DO $$
DECLARE
  v_function REGPROCEDURE;
BEGIN
  FOR v_function IN
    SELECT routines.oid::REGPROCEDURE
    FROM pg_catalog.pg_proc AS routines
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = routines.pronamespace
    WHERE schemas.nspname = 'public'
      AND NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_depend AS dependencies
        WHERE dependencies.classid = 'pg_proc'::REGCLASS
          AND dependencies.objid = routines.oid
          AND dependencies.deptype = 'e'
      )
  LOOP
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM PUBLIC, aurum_support',
      v_function
    );
    EXECUTE pg_catalog.format(
      'GRANT EXECUTE ON FUNCTION %s TO aurum_support',
      v_function
    );
  END LOOP;
END
$$
""",
)


DEFAULT_PRIVILEGES_SQL = tuple(
    f"""
ALTER DEFAULT PRIVILEGES FOR ROLE {owner}{scope}
  REVOKE ALL PRIVILEGES ON {object_type} FROM {grantees}
"""
    for owner, grantees in (
        ("aurum_schema_owner", "PUBLIC, aurum_app, aurum_support"),
        ("aurum_migrator", "PUBLIC, aurum_app, aurum_support"),
        ("aurum_support", "PUBLIC, aurum_app"),
    )
    for scope in ("", " IN SCHEMA public")
    for object_type in ("TABLES", "SEQUENCES", "FUNCTIONS", "TYPES")
)


POSTCONDITION_SQL = """
DO $$
DECLARE
  v_bad_object TEXT;
  v_actual TEXT[];
  v_expected TEXT[];
  v_relation RECORD;
BEGIN
  IF (
    SELECT pg_catalog.pg_get_userbyid(nspowner)
    FROM pg_catalog.pg_namespace
    WHERE nspname = 'public'
  ) IS DISTINCT FROM 'aurum_schema_owner' THEN
    RAISE EXCEPTION 'public schema ownership was not transferred';
  END IF;

  IF NOT pg_catalog.has_database_privilege(
    'aurum_support', current_database(), 'CONNECT'
  ) OR pg_catalog.has_database_privilege(
    'aurum_support', current_database(), 'CREATE'
  ) OR pg_catalog.has_database_privilege(
    'aurum_support', current_database(), 'TEMP'
  ) THEN
    RAISE EXCEPTION 'aurum_support database privileges exceed CONNECT';
  END IF;

  IF NOT pg_catalog.has_schema_privilege(
    'aurum_support', 'public', 'USAGE'
  ) OR pg_catalog.has_schema_privilege(
    'aurum_support', 'public', 'CREATE'
  ) THEN
    RAISE EXCEPTION 'aurum_support schema privileges exceed USAGE';
  END IF;

  SELECT pg_catalog.format('%I.%I', schemas.nspname, relations.relname)
  INTO v_bad_object
  FROM pg_catalog.pg_class AS relations
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = relations.relnamespace
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = relations.relowner
  WHERE schemas.nspname = 'public'
    AND relations.relpersistence <> 't'
    AND owners.rolname <> 'aurum_schema_owner'
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'application relation was not transferred: %', v_bad_object;
  END IF;

  SELECT pg_catalog.format(
    '%I.%I(%s)',
    schemas.nspname,
    routines.proname,
    pg_catalog.pg_get_function_identity_arguments(routines.oid)
  )
  INTO v_bad_object
  FROM pg_catalog.pg_proc AS routines
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = routines.pronamespace
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = routines.proowner
  WHERE schemas.nspname = 'public'
    AND NOT EXISTS (
      SELECT 1
      FROM pg_catalog.pg_depend AS dependencies
      WHERE dependencies.classid = 'pg_proc'::REGCLASS
        AND dependencies.objid = routines.oid
        AND dependencies.deptype = 'e'
    )
    AND owners.rolname <> 'aurum_schema_owner'
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'application function was not transferred: %', v_bad_object;
  END IF;

  SELECT extensions.extname
  INTO v_bad_object
  FROM pg_catalog.pg_extension AS extensions
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = extensions.extowner
  WHERE extensions.extname IN ('pgcrypto', 'pg_trgm', 'unaccent')
    AND owners.rolname <> 'aurum_schema_owner'
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'extension ownership was not transferred: %', v_bad_object;
  END IF;

  FOR v_relation IN
    SELECT relations.relname, relations.relkind
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = relations.relnamespace
    WHERE schemas.nspname = 'public'
      AND relations.relkind IN ('r', 'p', 'v', 'm')
  LOOP
    SELECT pg_catalog.array_agg(
      grants.privilege_type ORDER BY grants.privilege_type
    )
    INTO v_actual
    FROM information_schema.role_table_grants AS grants
    WHERE grants.table_schema = 'public'
      AND grants.table_name = v_relation.relname
      AND grants.grantee = 'aurum_support';

    IF v_relation.relname = 'alembic_version' THEN
      v_expected := NULL;
    ELSIF v_relation.relname = 'audit_log' OR v_relation.relkind IN ('v', 'm') THEN
      v_expected := ARRAY['SELECT']::TEXT[];
    ELSE
      v_expected := ARRAY['DELETE', 'INSERT', 'SELECT', 'UPDATE']::TEXT[];
    END IF;

    IF v_actual IS DISTINCT FROM v_expected THEN
      RAISE EXCEPTION
        'unexpected aurum_support privileges on public.%: %, expected %',
        v_relation.relname,
        v_actual,
        v_expected;
    END IF;
  END LOOP;

  IF EXISTS (
    SELECT 1
    FROM information_schema.role_table_grants
    WHERE grantee IN ('aurum_app', 'aurum_support')
      AND is_grantable = 'YES'
  ) OR EXISTS (
    SELECT 1
    FROM information_schema.role_routine_grants
    WHERE grantee IN ('aurum_app', 'aurum_support')
      AND is_grantable = 'YES'
  ) THEN
    RAISE EXCEPTION 'runtime roles must not receive privileges with grant option';
  END IF;

  SELECT routines.oid::REGPROCEDURE::TEXT
  INTO v_bad_object
  FROM pg_catalog.pg_proc AS routines
  JOIN pg_catalog.pg_namespace AS schemas
    ON schemas.oid = routines.pronamespace
  WHERE schemas.nspname = 'public'
    AND NOT EXISTS (
      SELECT 1
      FROM pg_catalog.pg_depend AS dependencies
      WHERE dependencies.classid = 'pg_proc'::REGCLASS
        AND dependencies.objid = routines.oid
        AND dependencies.deptype = 'e'
    )
    AND (
      NOT pg_catalog.has_function_privilege(
        'aurum_support', routines.oid, 'EXECUTE'
      )
      OR pg_catalog.has_function_privilege(
        'aurum_support', routines.oid, 'EXECUTE WITH GRANT OPTION'
      )
    )
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'invalid support execute privilege on %', v_bad_object;
  END IF;

  SELECT owners.rolname || ':' || defaults.defaclobjtype::TEXT
  INTO v_bad_object
  FROM pg_catalog.pg_default_acl AS defaults
  JOIN pg_catalog.pg_roles AS owners
    ON owners.oid = defaults.defaclrole
  CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS acl
  LEFT JOIN pg_catalog.pg_roles AS grantees
    ON grantees.oid = acl.grantee
  WHERE owners.rolname IN ('aurum_schema_owner', 'aurum_migrator')
    AND (
      acl.grantee = 0
      OR grantees.rolname IN ('aurum_app', 'aurum_support')
    )
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION 'unsafe default privilege remains for %', v_bad_object;
  END IF;
END
$$
"""


DOWNGRADE_PREFLIGHT_SQL = """
DO $$
DECLARE
  v_bad_object TEXT;
BEGIN
  IF session_user <> 'aurum_migrator' OR current_user <> 'aurum_migrator' THEN
    RAISE EXCEPTION 'revision 0067 downgrade must run directly as aurum_migrator';
  END IF;
  IF current_database() NOT LIKE '%\\_test' ESCAPE '\\' THEN
    RAISE EXCEPTION 'revision 0067 downgrade is allowed only on a *_test database';
  END IF;

  SELECT shared_object
  INTO v_bad_object
  FROM (
    SELECT 'database:' || pg_catalog.quote_ident(databases.datname) AS shared_object
    FROM pg_catalog.pg_database AS databases
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = databases.datdba
    WHERE owners.rolname = 'aurum_schema_owner'
      AND databases.datname <> current_database()

    UNION ALL

    SELECT 'tablespace:' || pg_catalog.quote_ident(tablespaces.spcname)
    FROM pg_catalog.pg_tablespace AS tablespaces
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = tablespaces.spcowner
    WHERE owners.rolname = 'aurum_schema_owner'
  ) AS shared_objects
  LIMIT 1;

  IF v_bad_object IS NOT NULL THEN
    RAISE EXCEPTION
      'refusing cluster-wide ownership transfer; aurum_schema_owner owns %',
      v_bad_object;
  END IF;
END
$$
"""


DOWNGRADE_SQL = (
    # PostgreSQL requires the target owner to create objects in their schema.
    # The later legacy grants deliberately retain this privilege for support.
    "GRANT CREATE ON SCHEMA public TO aurum_support",
    "REASSIGN OWNED BY aurum_schema_owner TO aurum_support",
    """
DO $$
BEGIN
  EXECUTE pg_catalog.format(
    'REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC, aurum_app, aurum_migrator',
    current_database()
  );
  EXECUTE pg_catalog.format(
    'GRANT CONNECT ON DATABASE %I TO aurum_app, aurum_migrator',
    current_database()
  );
  EXECUTE pg_catalog.format(
    'GRANT ALL PRIVILEGES ON DATABASE %I TO aurum_support',
    current_database()
  );
END
$$
""",
    """
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC, aurum_app, aurum_migrator;
""",
    "GRANT USAGE ON SCHEMA public TO aurum_app",
    "GRANT ALL PRIVILEGES ON SCHEMA public TO aurum_support",
)


def upgrade() -> None:
    op.execute(PREFLIGHT_SQL)
    for statement in TRANSFER_AND_GRANTS_SQL:
        op.execute(statement)
    for statement in DEFAULT_PRIVILEGES_SQL:
        op.execute(statement)
    op.execute(POSTCONDITION_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_PREFLIGHT_SQL)
    for statement in DOWNGRADE_SQL:
        op.execute(statement)
