\getenv app_password AURUM_APP_PASSWORD
\getenv support_password AURUM_SUPPORT_PASSWORD
\getenv mailer_password AURUM_MAILER_PASSWORD
\getenv billing_worker_password AURUM_BILLING_WORKER_PASSWORD
\getenv migrator_password AURUM_MIGRATOR_PASSWORD
\getenv database_name POSTGRES_DB

-- The revision ledger may be absent only before the first migration. Extension
-- objects created by init.sh are allowed, but any application object means the
-- ledger was removed or renamed and bootstrap must stop without changing ACLs.
DO $$
DECLARE
    unexpected_object TEXT;
BEGIN
    IF pg_catalog.to_regclass('public.alembic_version') IS NOT NULL THEN
        RETURN;
    END IF;

    SELECT object_name
    INTO unexpected_object
    FROM (
        SELECT 'schema:' || pg_catalog.quote_ident(schemas.nspname) AS object_name
        FROM pg_catalog.pg_namespace AS schemas
        WHERE schemas.nspname NOT IN ('public', 'information_schema')
          AND schemas.nspname !~ '^pg_'

        UNION ALL

        SELECT pg_catalog.format(
            'relation:%I.%I',
            schemas.nspname,
            relations.relname
        )
        FROM pg_catalog.pg_class AS relations
        JOIN pg_catalog.pg_namespace AS schemas
          ON schemas.oid = relations.relnamespace
        WHERE schemas.nspname NOT IN ('pg_catalog', 'information_schema')
          AND schemas.nspname !~ '^pg_toast'
          AND relations.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend AS dependencies
              WHERE dependencies.classid = 'pg_class'::REGCLASS
                AND dependencies.objid = relations.oid
                AND dependencies.deptype = 'e'
          )

        UNION ALL

        SELECT pg_catalog.format(
            'routine:%I.%I',
            schemas.nspname,
            routines.proname
        )
        FROM pg_catalog.pg_proc AS routines
        JOIN pg_catalog.pg_namespace AS schemas
          ON schemas.oid = routines.pronamespace
        WHERE schemas.nspname NOT IN ('pg_catalog', 'information_schema')
          AND schemas.nspname !~ '^pg_'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend AS dependencies
              WHERE dependencies.classid = 'pg_proc'::REGCLASS
                AND dependencies.objid = routines.oid
                AND dependencies.deptype = 'e'
          )

        UNION ALL

        SELECT pg_catalog.format(
            'type:%I.%I',
            schemas.nspname,
            types.typname
        )
        FROM pg_catalog.pg_type AS types
        JOIN pg_catalog.pg_namespace AS schemas
          ON schemas.oid = types.typnamespace
        WHERE schemas.nspname NOT IN ('pg_catalog', 'information_schema')
          AND schemas.nspname !~ '^pg_'
          AND types.typtype IN ('d', 'e')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_depend AS dependencies
              WHERE dependencies.classid = 'pg_type'::REGCLASS
                AND dependencies.objid = types.oid
                AND dependencies.deptype = 'e'
          )

        UNION ALL

        SELECT 'extension:' || pg_catalog.quote_ident(extensions.extname)
        FROM pg_catalog.pg_extension AS extensions
        WHERE extensions.extname NOT IN (
            'plpgsql',
            'pgcrypto',
            'pg_trgm',
            'unaccent'
        )
    ) AS unexpected_objects
    LIMIT 1;

    IF unexpected_object IS NOT NULL THEN
        RAISE EXCEPTION
            'Alembic revision ledger is missing from a non-empty database (%)',
            unexpected_object;
    END IF;
END
$$;

-- Ownership grants implicit privileges that REVOKE cannot remove. Detect any
-- pre-existing restricted-runtime ownership before changing roles, memberships,
-- or ACLs.
DO $$
DECLARE
    unsafe_owned_object TEXT;
BEGIN
    SELECT object_name
    INTO unsafe_owned_object
    FROM (
        SELECT 'database:' || pg_catalog.quote_ident(databases.datname) AS object_name
        FROM pg_catalog.pg_database AS databases
        JOIN pg_catalog.pg_roles AS owners
          ON owners.oid = databases.datdba
        WHERE owners.rolname IN (
            'aurum_mailer',
            'aurum_billing_worker',
            'aurum_edge_cash_executor',
            'aurum_edge_cash_owner'
        )

        UNION ALL

        SELECT 'tablespace:' || pg_catalog.quote_ident(tablespaces.spcname)
        FROM pg_catalog.pg_tablespace AS tablespaces
        JOIN pg_catalog.pg_roles AS owners
          ON owners.oid = tablespaces.spcowner
        WHERE owners.rolname IN (
            'aurum_mailer',
            'aurum_billing_worker',
            'aurum_edge_cash_executor',
            'aurum_edge_cash_owner'
        )

        UNION ALL

        SELECT 'schema:' || pg_catalog.quote_ident(schemas.nspname)
        FROM pg_catalog.pg_namespace AS schemas
        JOIN pg_catalog.pg_roles AS owners
          ON owners.oid = schemas.nspowner
        WHERE schemas.nspname <> 'information_schema'
          AND schemas.nspname !~ '^pg_'
          AND owners.rolname IN (
              'aurum_mailer',
              'aurum_billing_worker',
              'aurum_edge_cash_executor',
              'aurum_edge_cash_owner'
          )

        UNION ALL

        SELECT 'relation:' || pg_catalog.format(
            '%I.%I', schemas.nspname, relations.relname
        )
        FROM pg_catalog.pg_class AS relations
        JOIN pg_catalog.pg_namespace AS schemas
          ON schemas.oid = relations.relnamespace
        JOIN pg_catalog.pg_roles AS owners
          ON owners.oid = relations.relowner
        WHERE schemas.nspname <> 'information_schema'
          AND schemas.nspname !~ '^pg_'
          AND owners.rolname IN (
              'aurum_mailer',
              'aurum_billing_worker',
              'aurum_edge_cash_executor',
              'aurum_edge_cash_owner'
          )

        UNION ALL

        SELECT 'function:' || routines.oid::REGPROCEDURE::TEXT
        FROM pg_catalog.pg_proc AS routines
        JOIN pg_catalog.pg_namespace AS schemas
          ON schemas.oid = routines.pronamespace
        JOIN pg_catalog.pg_roles AS owners
          ON owners.oid = routines.proowner
        WHERE schemas.nspname <> 'information_schema'
          AND schemas.nspname !~ '^pg_'
          AND owners.rolname IN (
              'aurum_mailer',
              'aurum_billing_worker',
              'aurum_edge_cash_executor',
              'aurum_edge_cash_owner'
          )

        UNION ALL

        SELECT 'type:' || pg_catalog.format(
            '%I.%I', schemas.nspname, types.typname
        )
        FROM pg_catalog.pg_type AS types
        JOIN pg_catalog.pg_namespace AS schemas
          ON schemas.oid = types.typnamespace
        JOIN pg_catalog.pg_roles AS owners
          ON owners.oid = types.typowner
        WHERE schemas.nspname <> 'information_schema'
          AND schemas.nspname !~ '^pg_'
          AND owners.rolname IN (
              'aurum_mailer',
              'aurum_billing_worker',
              'aurum_edge_cash_executor',
              'aurum_edge_cash_owner'
          )
    ) AS unsafe_owned_objects
    LIMIT 1;

    IF unsafe_owned_object IS NOT NULL THEN
        RAISE EXCEPTION
            'Restricted runtime role owns forbidden object %',
            unsafe_owned_object;
    END IF;
END
$$;

DO $$
DECLARE
    unsafe_default_acl TEXT;
BEGIN
    SELECT pg_catalog.format(
        'owner=%I object_type=%s', owners.rolname, defaults.defaclobjtype
    )
    INTO unsafe_default_acl
    FROM pg_catalog.pg_default_acl AS defaults
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = defaults.defaclrole
    CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS acl
    JOIN pg_catalog.pg_roles AS grantees
      ON grantees.oid = acl.grantee
    WHERE grantees.rolname IN (
        'aurum_mailer',
        'aurum_billing_worker',
        'aurum_edge_cash_executor',
        'aurum_edge_cash_owner'
    )
    LIMIT 1;

    IF unsafe_default_acl IS NOT NULL THEN
        RAISE EXCEPTION
            'Restricted runtime role has forbidden default privilege (%)',
            unsafe_default_acl;
    END IF;
END
$$;

SELECT 'CREATE ROLE aurum_app'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'aurum_app'
)
\gexec

SELECT 'CREATE ROLE aurum_support'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'aurum_support'
)
\gexec

SELECT 'CREATE ROLE aurum_mailer'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'aurum_mailer'
)
\gexec

SELECT 'CREATE ROLE aurum_billing_worker'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'aurum_billing_worker'
)
\gexec

SELECT 'CREATE ROLE aurum_schema_owner'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'aurum_schema_owner'
)
\gexec

SELECT 'CREATE ROLE aurum_migrator'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'aurum_migrator'
)
\gexec

SELECT 'CREATE ROLE aurum_edge_cash_executor'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'aurum_edge_cash_executor'
)
\gexec

SELECT 'CREATE ROLE aurum_edge_cash_owner'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'aurum_edge_cash_owner'
)
\gexec

ALTER ROLE aurum_app WITH
    LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'app_password';
ALTER ROLE aurum_support WITH
    LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS
    PASSWORD :'support_password';
ALTER ROLE aurum_mailer WITH
    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 4
    PASSWORD :'mailer_password';
ALTER ROLE aurum_billing_worker WITH
    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 2
    PASSWORD :'billing_worker_password';
ALTER ROLE aurum_billing_worker SET statement_timeout = '30s';
ALTER ROLE aurum_billing_worker SET lock_timeout = '5s';
ALTER ROLE aurum_billing_worker SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE aurum_schema_owner WITH
    NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS;
ALTER ROLE aurum_migrator WITH
    LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'migrator_password';
ALTER ROLE aurum_edge_cash_executor WITH
    NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD NULL;
ALTER ROLE aurum_edge_cash_owner WITH
    NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD NULL;

-- This contract runs as the PostgreSQL bootstrap superuser. The target role
-- never needs CREATEDB, even transiently, to receive this one fixed database.
ALTER DATABASE :"database_name" OWNER TO aurum_schema_owner;

GRANT aurum_schema_owner TO aurum_migrator
    WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;
GRANT aurum_support TO aurum_migrator
    WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;
GRANT aurum_edge_cash_owner TO aurum_migrator
    WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;

-- A database added to an existing cluster does not pass through init.sh.
-- Normalize the future schema-owner objects here as well so functions never
-- inherit PostgreSQL's implicit PUBLIC EXECUTE privilege.
ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner
    REVOKE ALL ON TABLES FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner
    REVOKE ALL ON SEQUENCES FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner
    REVOKE ALL ON FUNCTIONS FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE aurum_schema_owner IN SCHEMA public
    REVOKE ALL ON FUNCTIONS FROM PUBLIC, aurum_app, aurum_support, aurum_billing_worker;

SELECT pg_catalog.format(
    'REVOKE %I FROM %I',
    granted.rolname,
    member.rolname
)
FROM pg_catalog.pg_auth_members AS membership
JOIN pg_catalog.pg_roles AS granted
  ON granted.oid = membership.roleid
JOIN pg_catalog.pg_roles AS member
  ON member.oid = membership.member
WHERE (
    granted.rolname IN (
        'aurum_schema_owner',
        'aurum_migrator',
        'aurum_edge_cash_owner'
    )
    AND member.rolname IN (
        'aurum_app',
        'aurum_support',
        'aurum_edge_cash_executor'
    )
) OR (
    granted.rolname = 'aurum_support'
    AND member.rolname IN (
        'aurum_app', 'aurum_mailer', 'aurum_billing_worker',
        'aurum_edge_cash_executor'
    )
) OR (
    granted.rolname = 'aurum_mailer' OR member.rolname = 'aurum_mailer'
) OR (
    granted.rolname = 'aurum_billing_worker'
    OR member.rolname = 'aurum_billing_worker'
) OR (
    (
        granted.rolname IN (
            'aurum_edge_cash_executor',
            'aurum_edge_cash_owner'
        )
        OR member.rolname IN (
            'aurum_edge_cash_executor',
            'aurum_edge_cash_owner'
        )
    )
    AND NOT (
        granted.rolname = 'aurum_edge_cash_owner'
        AND member.rolname = 'aurum_migrator'
    )
)
\gexec

REVOKE ALL PRIVILEGES ON DATABASE :"database_name"
    FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, aurum_billing_worker,
         aurum_migrator,
         aurum_edge_cash_executor, aurum_edge_cash_owner;
GRANT CONNECT ON DATABASE :"database_name"
    TO aurum_app, aurum_mailer, aurum_billing_worker, aurum_migrator;

DO $$
DECLARE
    application_schema TEXT;
BEGIN
    FOR application_schema IN
        SELECT schemas.nspname
        FROM pg_catalog.pg_namespace AS schemas
        WHERE schemas.nspname <> 'information_schema'
          AND schemas.nspname !~ '^pg_'
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON SCHEMA %I '
            'FROM aurum_mailer, aurum_billing_worker, '
            'aurum_edge_cash_executor, aurum_edge_cash_owner',
            application_schema
        );
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I '
            'FROM aurum_mailer, aurum_billing_worker, '
            'aurum_edge_cash_executor, aurum_edge_cash_owner',
            application_schema
        );
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I '
            'FROM aurum_mailer, aurum_billing_worker, '
            'aurum_edge_cash_executor, aurum_edge_cash_owner',
            application_schema
        );
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA %I '
            'FROM aurum_mailer, aurum_billing_worker, '
            'aurum_edge_cash_executor, aurum_edge_cash_owner',
            application_schema
        );
    END LOOP;
END
$$;

DO $$
DECLARE
    current_revision TEXT;
    revision_count BIGINT;
    revision_number INTEGER;
    requires_legacy_schema_owner BOOLEAN := FALSE;
BEGIN
    IF pg_catalog.to_regclass('public.alembic_version') IS NULL THEN
        -- A fresh database has no revision ledger yet. Legacy migrations
        -- through 0066 require the support role to own and create objects.
        EXECUTE pg_catalog.format(
            'GRANT ALL PRIVILEGES ON DATABASE %I TO aurum_support',
            current_database()
        );
        requires_legacy_schema_owner := TRUE;
    ELSE
        SELECT pg_catalog.count(*), pg_catalog.min(version_num)
        INTO revision_count, current_revision
        FROM public.alembic_version;

        IF revision_count <> 1 OR current_revision IS NULL THEN
            RAISE EXCEPTION
                'Alembic revision ledger must contain exactly one row';
        END IF;
        IF current_revision !~ '^[0-9]{4}$' THEN
            RAISE EXCEPTION
                'Unsupported Alembic revision format in database role bootstrap';
        END IF;

        revision_number := current_revision::INTEGER;
        IF revision_number < 1 OR revision_number > 104 THEN
            RAISE EXCEPTION
                'Unknown Alembic revision in database role bootstrap: %',
                current_revision;
        ELSIF revision_number >= 67 THEN
            EXECUTE pg_catalog.format(
                'GRANT CONNECT ON DATABASE %I TO aurum_support',
                current_database()
            );
        ELSE
            EXECUTE pg_catalog.format(
                'GRANT ALL PRIVILEGES ON DATABASE %I TO aurum_support',
                current_database()
            );
            requires_legacy_schema_owner := TRUE;
        END IF;
    END IF;

    IF requires_legacy_schema_owner THEN
        ALTER SCHEMA public OWNER TO aurum_support;
        REVOKE CREATE ON SCHEMA public FROM PUBLIC;
        GRANT USAGE ON SCHEMA public TO aurum_app;
        GRANT ALL ON SCHEMA public TO aurum_support;
    END IF;
END
$$;

-- Extension implementation functions are commonly owned by postgres rather
-- than the extension owner. Only the cluster bootstrap can normalize their
-- ACLs on an existing installation.
DO $$
DECLARE
    extension_function REGPROCEDURE;
BEGIN
    FOR extension_function IN
        SELECT routines.oid::REGPROCEDURE
        FROM pg_catalog.pg_proc AS routines
        JOIN pg_catalog.pg_depend AS dependencies
          ON dependencies.classid = 'pg_proc'::REGCLASS
         AND dependencies.objid = routines.oid
         AND dependencies.deptype = 'e'
        JOIN pg_catalog.pg_extension AS extensions
          ON extensions.oid = dependencies.refobjid
        WHERE extensions.extname IN ('pgcrypto', 'pg_trgm', 'unaccent')
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s '
            'FROM PUBLIC, aurum_app, aurum_support, aurum_mailer, '
            'aurum_billing_worker, '
            'aurum_edge_cash_executor, aurum_edge_cash_owner',
            extension_function
        );
    END LOOP;

    IF pg_catalog.to_regprocedure('public.similarity_op(text,text)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION public.similarity_op(TEXT, TEXT)
            TO aurum_app, aurum_support, aurum_schema_owner;
    END IF;
    IF pg_catalog.to_regprocedure('public.gen_random_uuid()') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION public.gen_random_uuid()
            TO aurum_app, aurum_support, aurum_schema_owner;
    END IF;
    IF pg_catalog.to_regprocedure('public.pgp_sym_encrypt(text,text)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION public.pgp_sym_encrypt(TEXT, TEXT)
            TO aurum_support, aurum_schema_owner;
        GRANT EXECUTE ON FUNCTION public.pgp_sym_encrypt(TEXT, TEXT, TEXT)
            TO aurum_support, aurum_schema_owner;
        GRANT EXECUTE ON FUNCTION public.pgp_sym_decrypt(BYTEA, TEXT)
            TO aurum_support, aurum_schema_owner;
        GRANT EXECUTE ON FUNCTION public.pgp_sym_decrypt(BYTEA, TEXT, TEXT)
            TO aurum_support, aurum_schema_owner;
    END IF;
END
$$;

DO $$
DECLARE
    current_revision TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_authid
        WHERE rolname = 'aurum_billing_worker'
          AND rolcanlogin
          AND NOT rolinherit
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
          AND rolconnlimit = 2
          AND rolpassword IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'aurum_billing_worker violates the deny-by-default contract';
    END IF;

    IF pg_catalog.to_regclass('public.alembic_version') IS NOT NULL THEN
        SELECT version_num INTO current_revision FROM public.alembic_version;
        IF current_revision ~ '^[0-9]{4}$' AND current_revision::INTEGER >= 104 THEN
            GRANT USAGE ON SCHEMA public TO aurum_billing_worker;
            GRANT EXECUTE ON FUNCTION public.process_billing_trial_endings(INTEGER)
                TO aurum_billing_worker;
            GRANT EXECUTE ON FUNCTION public.process_billing_grace_endings(INTEGER)
                TO aurum_billing_worker;
        END IF;
    END IF;
END
$$;

DO $$
DECLARE
    current_revision TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_authid
        WHERE rolname = 'aurum_mailer'
          AND rolcanlogin
          AND NOT rolinherit
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
          AND rolpassword IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'aurum_mailer violates the deny-by-default contract';
    END IF;

    IF pg_catalog.to_regclass('public.alembic_version') IS NOT NULL THEN
        SELECT version_num INTO current_revision FROM public.alembic_version;
        IF current_revision ~ '^[0-9]{4}$' AND current_revision::INTEGER >= 91 THEN
            GRANT USAGE ON SCHEMA public TO aurum_mailer;
            GRANT EXECUTE ON FUNCTION public.claim_platform_invitation_email(JSONB, INTEGER)
                TO aurum_mailer;
            GRANT EXECUTE ON FUNCTION public.complete_platform_invitation_email(
                UUID, UUID, TEXT, TEXT
            ) TO aurum_mailer;
        END IF;
    END IF;
END
$$;

DO $$
DECLARE
    unsafe_object TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_authid
        WHERE rolname = 'aurum_edge_cash_executor'
          AND NOT rolcanlogin
          AND rolinherit
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
          AND rolpassword IS NULL
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_authid
        WHERE rolname = 'aurum_edge_cash_owner'
          AND NOT rolcanlogin
          AND rolinherit
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
          AND rolpassword IS NULL
    ) THEN
        RAISE EXCEPTION 'Edge cash roles violate the deny-by-default contract';
    END IF;

    SELECT pg_catalog.quote_ident(schemas.nspname)
    INTO unsafe_object
    FROM pg_catalog.pg_namespace AS schemas
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = schemas.nspowner
    WHERE schemas.nspname <> 'information_schema'
      AND schemas.nspname !~ '^pg_'
      AND owners.rolname IN (
        'aurum_edge_cash_executor',
        'aurum_edge_cash_owner'
      )
    LIMIT 1;

    IF unsafe_object IS NOT NULL THEN
        RAISE EXCEPTION 'Edge cash role owns application schema %', unsafe_object;
    END IF;

    SELECT pg_catalog.format('%I.%I', schemas.nspname, relations.relname)
    INTO unsafe_object
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = relations.relnamespace
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = relations.relowner
    WHERE schemas.nspname <> 'information_schema'
      AND schemas.nspname !~ '^pg_'
      AND owners.rolname IN (
        'aurum_edge_cash_executor',
        'aurum_edge_cash_owner'
      )
    LIMIT 1;

    IF unsafe_object IS NOT NULL THEN
        RAISE EXCEPTION 'Edge cash role owns application relation %', unsafe_object;
    END IF;

    SELECT routines.oid::REGPROCEDURE::TEXT
    INTO unsafe_object
    FROM pg_catalog.pg_proc AS routines
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = routines.pronamespace
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = routines.proowner
    WHERE schemas.nspname <> 'information_schema'
      AND schemas.nspname !~ '^pg_'
      AND owners.rolname IN (
        'aurum_edge_cash_executor',
        'aurum_edge_cash_owner'
      )
    LIMIT 1;

    IF unsafe_object IS NOT NULL THEN
        RAISE EXCEPTION 'Edge cash role owns application function %', unsafe_object;
    END IF;

    SELECT types.oid::REGTYPE::TEXT
    INTO unsafe_object
    FROM pg_catalog.pg_type AS types
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = types.typnamespace
    JOIN pg_catalog.pg_roles AS owners
      ON owners.oid = types.typowner
    WHERE schemas.nspname <> 'information_schema'
      AND schemas.nspname !~ '^pg_'
      AND owners.rolname IN (
        'aurum_edge_cash_executor',
        'aurum_edge_cash_owner'
      )
    LIMIT 1;

    IF unsafe_object IS NOT NULL THEN
        RAISE EXCEPTION 'Edge cash role owns application type %', unsafe_object;
    END IF;
END
$$;
