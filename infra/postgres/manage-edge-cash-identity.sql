SELECT pg_catalog.set_config(
    'aurum.bootstrap.edge_node_id',
    :'edge_node_id',
    FALSE
) AS configured_edge_node_id
\gset
SELECT pg_catalog.set_config(
    'aurum.bootstrap.edge_identity_action',
    :'identity_action',
    FALSE
) AS configured_edge_identity_action
\gset

DO $edge_identity$
DECLARE
    requested_node_id UUID := pg_catalog.current_setting(
        'aurum.bootstrap.edge_node_id'
    )::UUID;
    requested_action TEXT := pg_catalog.current_setting(
        'aurum.bootstrap.edge_identity_action'
    );
    node_record RECORD;
    identity_record RECORD;
    expected_role TEXT;
    current_role_oid OID;
    executor_oid OID;
    unsafe_owned_object TEXT;
    object_record RECORD;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = session_user AND rolsuper
    ) THEN
        RAISE EXCEPTION 'Edge identity management requires the bootstrap superuser';
    END IF;
    IF requested_action NOT IN ('enroll', 'revoke') THEN
        RAISE EXCEPTION 'Unsupported Edge identity action';
    END IF;
    IF pg_catalog.to_regclass('public.edge_cash_node_identity') IS NULL THEN
        RAISE EXCEPTION 'Edge cash identity ledger is not installed';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'aurum:edge-cash-identity:' || requested_node_id::TEXT,
            0
        )
    );

    SELECT
        node.id,
        node.tenant_id,
        node.branch_id,
        node.register_id,
        node.node_kind,
        node.mode,
        node.status
    INTO node_record
    FROM public.sync_node AS node
    WHERE node.id = requested_node_id
    FOR UPDATE;

    IF node_record.id IS NULL
       OR node_record.node_kind <> 'edge'
       OR node_record.mode <> 'edge_writer'
       OR node_record.register_id IS NULL THEN
        RAISE EXCEPTION 'Eligible Edge writer node was not found';
    END IF;

    expected_role := 'aurum_edge_node_' || pg_catalog.replace(
        requested_node_id::TEXT,
        '-',
        ''
    );

    SELECT identity.*
    INTO identity_record
    FROM public.edge_cash_node_identity AS identity
    WHERE identity.edge_node_id = requested_node_id;

    SELECT roles.oid
    INTO current_role_oid
    FROM pg_catalog.pg_roles AS roles
    WHERE roles.rolname = expected_role;

    IF current_role_oid IS NOT NULL THEN
        SELECT owned_object
        INTO unsafe_owned_object
        FROM (
            SELECT 'database:' || databases.datname AS owned_object
            FROM pg_catalog.pg_database AS databases
            WHERE databases.datdba = current_role_oid

            UNION ALL

            SELECT 'tablespace:' || tablespaces.spcname
            FROM pg_catalog.pg_tablespace AS tablespaces
            WHERE tablespaces.spcowner = current_role_oid

            UNION ALL

            SELECT 'schema:' || schemas.nspname
            FROM pg_catalog.pg_namespace AS schemas
            WHERE schemas.nspowner = current_role_oid

            UNION ALL

            SELECT 'relation:' || relations.oid::REGCLASS::TEXT
            FROM pg_catalog.pg_class AS relations
            WHERE relations.relowner = current_role_oid

            UNION ALL

            SELECT 'routine:' || routines.oid::REGPROCEDURE::TEXT
            FROM pg_catalog.pg_proc AS routines
            WHERE routines.proowner = current_role_oid

            UNION ALL

            SELECT 'type:' || types.oid::REGTYPE::TEXT
            FROM pg_catalog.pg_type AS types
            WHERE types.typowner = current_role_oid
        ) AS owned_objects
        LIMIT 1;

        IF unsafe_owned_object IS NOT NULL THEN
            RAISE EXCEPTION 'Edge database role owns forbidden object %',
                unsafe_owned_object;
        END IF;
        IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_default_acl AS defaults
            CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS acl
            WHERE acl.grantee = current_role_oid
        ) THEN
            RAISE EXCEPTION 'Edge database role has forbidden default privileges';
        END IF;
    END IF;

    IF requested_action = 'enroll' THEN
        IF node_record.status <> 'active' THEN
            RAISE EXCEPTION 'Only an active Edge writer can be enrolled';
        END IF;

        IF identity_record.id IS NOT NULL THEN
            IF identity_record.tenant_id <> node_record.tenant_id
               OR identity_record.branch_id <> node_record.branch_id
               OR identity_record.register_id <> node_record.register_id
               OR identity_record.database_role <> expected_role
               OR current_role_oid IS NULL
               OR identity_record.database_role_oid <> current_role_oid THEN
                RAISE EXCEPTION 'Existing Edge identity binding does not match';
            END IF;
        ELSE
            IF current_role_oid IS NOT NULL THEN
                RAISE EXCEPTION 'Unbound Edge database role already exists';
            END IF;

            EXECUTE pg_catalog.format(
                'CREATE ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB '
                'NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 1 '
                'PASSWORD NULL',
                expected_role
            );
            SELECT roles.oid
            INTO current_role_oid
            FROM pg_catalog.pg_roles AS roles
            WHERE roles.rolname = expected_role;

            EXECUTE pg_catalog.format(
                'GRANT aurum_edge_cash_executor TO %I '
                'WITH ADMIN FALSE, INHERIT TRUE, SET FALSE',
                expected_role
            );
            INSERT INTO public.edge_cash_node_identity (
                tenant_id,
                branch_id,
                edge_node_id,
                register_id,
                database_role,
                database_role_oid
            ) VALUES (
                node_record.tenant_id,
                node_record.branch_id,
                node_record.id,
                node_record.register_id,
                expected_role,
                current_role_oid
            );
        END IF;

        EXECUTE pg_catalog.format(
            'ALTER ROLE %I WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB '
            'NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 1 '
            'PASSWORD NULL',
            expected_role
        );
        EXECUTE pg_catalog.format(
            'ALTER ROLE %I SET statement_timeout = %L',
            expected_role,
            '30s'
        );
        EXECUTE pg_catalog.format(
            'ALTER ROLE %I SET lock_timeout = %L',
            expected_role,
            '5s'
        );
        EXECUTE pg_catalog.format(
            'ALTER ROLE %I SET idle_in_transaction_session_timeout = %L',
            expected_role,
            '30s'
        );

        SELECT roles.oid
        INTO executor_oid
        FROM pg_catalog.pg_roles AS roles
        WHERE roles.rolname = 'aurum_edge_cash_executor';
        IF executor_oid IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_auth_members AS memberships
            WHERE memberships.roleid = executor_oid
              AND memberships.member = current_role_oid
        ) THEN
            EXECUTE pg_catalog.format(
                'GRANT aurum_edge_cash_executor TO %I '
                'WITH ADMIN FALSE, INHERIT TRUE, SET FALSE',
                expected_role
            );
        END IF;
        IF executor_oid IS NULL OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_auth_members AS memberships
            WHERE memberships.roleid = executor_oid
              AND memberships.member = current_role_oid
              AND NOT memberships.admin_option
              AND memberships.inherit_option
              AND NOT memberships.set_option
        ) OR (
            SELECT pg_catalog.count(*)
            FROM pg_catalog.pg_auth_members AS memberships
            WHERE memberships.member = current_role_oid
        ) <> 1 THEN
            RAISE EXCEPTION 'Edge database role membership is unsafe';
        END IF;
    ELSE
        IF identity_record.id IS NULL
           OR identity_record.database_role <> expected_role
           OR current_role_oid IS NULL
           OR identity_record.database_role_oid <> current_role_oid THEN
            RAISE EXCEPTION 'Bound Edge identity was not found';
        END IF;
        IF node_record.status <> 'revoked' THEN
            RAISE EXCEPTION 'Edge node must be revoked before its identity is disabled';
        END IF;

        EXECUTE pg_catalog.format(
            'ALTER ROLE %I WITH NOLOGIN PASSWORD NULL',
            expected_role
        );
        IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_auth_members AS memberships
            JOIN pg_catalog.pg_roles AS granted
              ON granted.oid = memberships.roleid
            WHERE granted.rolname = 'aurum_edge_cash_executor'
              AND memberships.member = current_role_oid
        ) THEN
            EXECUTE pg_catalog.format(
                'REVOKE aurum_edge_cash_executor FROM %I',
                expected_role
            );
        END IF;
        FOR object_record IN
            SELECT granted.rolname AS role_name
            FROM pg_catalog.pg_auth_members AS memberships
            JOIN pg_catalog.pg_roles AS granted
              ON granted.oid = memberships.roleid
            WHERE memberships.member = current_role_oid
        LOOP
            EXECUTE pg_catalog.format(
                'REVOKE %I FROM %I',
                object_record.role_name,
                expected_role
            );
        END LOOP;
        FOR object_record IN
            SELECT member.rolname AS role_name
            FROM pg_catalog.pg_auth_members AS memberships
            JOIN pg_catalog.pg_roles AS member
              ON member.oid = memberships.member
            WHERE memberships.roleid = current_role_oid
        LOOP
            EXECUTE pg_catalog.format(
                'REVOKE %I FROM %I',
                expected_role,
                object_record.role_name
            );
        END LOOP;
        PERFORM pg_catalog.pg_terminate_backend(activity.pid)
        FROM pg_catalog.pg_stat_activity AS activity
        WHERE activity.usename = expected_role
          AND activity.pid <> pg_catalog.pg_backend_pid();
    END IF;

    FOR object_record IN
        SELECT databases.datname AS object_name
        FROM pg_catalog.pg_database AS databases
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I',
            object_record.object_name,
            expected_role
        );
    END LOOP;
    FOR object_record IN
        SELECT tablespaces.spcname AS object_name
        FROM pg_catalog.pg_tablespace AS tablespaces
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON TABLESPACE %I FROM %I',
            object_record.object_name,
            expected_role
        );
    END LOOP;
    FOR object_record IN
        SELECT schemas.nspname AS object_name
        FROM pg_catalog.pg_namespace AS schemas
        WHERE schemas.nspname <> 'information_schema'
          AND schemas.nspname !~ '^pg_'
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I',
            object_record.object_name,
            expected_role
        );
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM %I',
            object_record.object_name,
            expected_role
        );
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM %I',
            object_record.object_name,
            expected_role
        );
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA %I FROM %I',
            object_record.object_name,
            expected_role
        );
    END LOOP;
    FOR object_record IN
        SELECT types.oid::REGTYPE::TEXT AS object_name
        FROM pg_catalog.pg_type AS types
        CROSS JOIN LATERAL pg_catalog.aclexplode(types.typacl) AS acl
        WHERE acl.grantee = current_role_oid
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON TYPE %s FROM %I',
            object_record.object_name,
            expected_role
        );
    END LOOP;
    FOR object_record IN
        SELECT wrappers.fdwname AS object_name
        FROM pg_catalog.pg_foreign_data_wrapper AS wrappers
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON FOREIGN DATA WRAPPER %I FROM %I',
            object_record.object_name,
            expected_role
        );
    END LOOP;
    FOR object_record IN
        SELECT servers.srvname AS object_name
        FROM pg_catalog.pg_foreign_server AS servers
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON FOREIGN SERVER %I FROM %I',
            object_record.object_name,
            expected_role
        );
    END LOOP;
    FOR object_record IN
        SELECT metadata.oid AS object_oid
        FROM pg_catalog.pg_largeobject_metadata AS metadata
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON LARGE OBJECT %s FROM %I',
            object_record.object_oid,
            expected_role
        );
    END LOOP;
    FOR object_record IN
        SELECT parameters.parname AS object_name
        FROM pg_catalog.pg_parameter_acl AS parameters
        CROSS JOIN LATERAL pg_catalog.aclexplode(parameters.paracl) AS acl
        WHERE acl.grantee = current_role_oid
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON PARAMETER %I FROM %I',
            object_record.object_name,
            expected_role
        );
    END LOOP;
END
$edge_identity$;
