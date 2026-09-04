#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD_FILE:?POSTGRES_PASSWORD_FILE is required}"
: "${EDGE_IDENTITY_SCRIPT:?EDGE_IDENTITY_SCRIPT is required}"
: "${ROLE_BOOTSTRAP_SCRIPT:?ROLE_BOOTSTRAP_SCRIPT is required}"

[ "${CI:-}" = "true" ] \
    && [ "${GITHUB_ACTIONS:-}" = "true" ] \
    && [ "${AURUM_DISPOSABLE_DB_CONFIRM:-}" = "destroy-this-database" ] || {
    echo "Edge identity tooling tests are restricted to an explicitly disposable CI database" >&2
    exit 1
}

case "$POSTGRES_DB" in
    *_test) ;;
    *)
        echo "Edge identity tooling tests require a disposable *_test database" >&2
        exit 1
        ;;
esac

export PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-5}"
export PGOPTIONS="${PGOPTIONS:--c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=15000}"
unset \
    POSTGRES_PASSWORD \
    AURUM_APP_PASSWORD \
    AURUM_SUPPORT_PASSWORD \
    AURUM_MAILER_PASSWORD \
    AURUM_BILLING_WORKER_PASSWORD \
    AURUM_WORKER_PASSWORD \
    AURUM_MIGRATOR_PASSWORD \
    AURUM_BACKUP_PASSWORD \
    AURUM_PITR_PASSWORD
postgres_host="${POSTGRES_HOST:-127.0.0.1}"
postgres_port="${POSTGRES_PORT:-5432}"

psql_super() {
    psql -v ON_ERROR_STOP=1 -qAt \
        --host "$postgres_host" \
        --port "$postgres_port" \
        --username "$POSTGRES_USER" \
        --dbname "$POSTGRES_DB" \
        "$@"
}

edge_node_id="$(psql_super <<'SQL'
BEGIN;
ALTER TABLE public.branch DISABLE TRIGGER trg_branch_sync_writer;
WITH tenant_row AS (
    INSERT INTO public.tenant (name, contact_email, status)
    VALUES (
        'Edge identity tooling',
        'edge-identity-' || gen_random_uuid()::text || '@aurum.test',
        'active'
    )
    RETURNING id
), branch_row AS (
    INSERT INTO public.branch (tenant_id, name)
    SELECT id, 'Edge identity branch' FROM tenant_row
    RETURNING id, tenant_id
), register_row AS (
    INSERT INTO public.register (tenant_id, branch_id, name)
    SELECT tenant_id, id, 'Edge identity register' FROM branch_row
    RETURNING id, tenant_id, branch_id
), cloud_node AS (
    INSERT INTO public.sync_node (
        tenant_id, branch_id, node_kind, mode, status, display_name
    )
    SELECT
        tenant_id, branch_id, 'cloud', 'cloud_writer', 'active',
        'Edge identity cloud writer'
    FROM register_row
    RETURNING id, tenant_id, branch_id
), cloud_epoch AS (
    INSERT INTO public.sync_writer_epoch (
        tenant_id, branch_id, writer_epoch, activation_id,
        writer_node_id, allowed_register_id, capability, state,
        root_source_checksum, root_projection_checksum, last_sequence,
        current_source_checksum, current_projection_checksum,
        bootstrap_snapshot_hash, activation_manifest_hash,
        receipt_baseline_seq, prepared_at, activated_at
    )
    SELECT
        tenant_id,
        branch_id,
        1,
        gen_random_uuid(),
        id,
        NULL,
        'cloud_full',
        'active',
        repeat('0', 64),
        repeat('0', 64),
        0,
        repeat('0', 64),
        repeat('0', 64),
        repeat('0', 64),
        repeat('0', 64),
        0,
        now(),
        now()
    FROM cloud_node
    RETURNING tenant_id, branch_id, writer_node_id
), edge_node AS (
    INSERT INTO public.sync_node (
        tenant_id, branch_id, register_id, node_kind, mode, status,
        display_name, credential_kid, credential_hash,
        credential_issued_at, credential_expires_at,
        shadow_start_origin_node_id, shadow_start_writer_epoch,
        shadow_start_sequence, shadow_start_checksum,
        shadow_start_projection_checksum
    )
    SELECT
        register_row.tenant_id,
        register_row.branch_id,
        register_row.id,
        'edge',
        'edge_writer',
        'active',
        'Edge identity writer',
        gen_random_uuid(),
        replace(gen_random_uuid()::text, '-', '')
            || replace(gen_random_uuid()::text, '-', ''),
        now(),
        now() + interval '1 day',
        cloud_node.id,
        1,
        0,
        repeat('0', 64),
        repeat('0', 64)
    FROM register_row
    JOIN cloud_node
      ON cloud_node.tenant_id = register_row.tenant_id
     AND cloud_node.branch_id = register_row.branch_id
    JOIN cloud_epoch
      ON cloud_epoch.tenant_id = register_row.tenant_id
     AND cloud_epoch.branch_id = register_row.branch_id
    RETURNING id
)
SELECT id FROM edge_node;
ALTER TABLE public.branch ENABLE TRIGGER trg_branch_sync_writer;
COMMIT;
SQL
)"

[ -n "$edge_node_id" ] || {
    echo "Failed to create Edge identity test node" >&2
    exit 1
}
edge_role="aurum_edge_node_$(printf '%s' "$edge_node_id" | tr -d '-')"

run_identity_action() {
    EDGE_NODE_ID="$edge_node_id" \
    EDGE_IDENTITY_ACTION="$1" \
    POSTGRES_HOST="$postgres_host" \
    POSTGRES_PORT="$postgres_port" \
    sh "$EDGE_IDENTITY_SCRIPT"
}

run_role_bootstrap() {
    POSTGRES_HOST="$postgres_host" \
    POSTGRES_PORT="$postgres_port" \
    sh "$ROLE_BOOTSTRAP_SCRIPT"
}

# A pre-existing unbound role must never be adopted by enrollment.
psql_super -v edge_role="$edge_role" <<'SQL'
CREATE ROLE :"edge_role" NOLOGIN;
SQL
if run_identity_action enroll; then
    echo "Enrollment adopted a pre-existing unbound Edge role" >&2
    exit 1
fi
psql_super -v edge_role="$edge_role" <<'SQL'
DROP ROLE :"edge_role";
SQL

run_identity_action enroll &
first_enrollment_pid=$!
run_identity_action enroll &
second_enrollment_pid=$!
wait "$first_enrollment_pid"
wait "$second_enrollment_pid"
run_identity_action enroll

test "$(psql_super -c "
    SELECT count(*)
    FROM public.edge_cash_node_identity
    WHERE edge_node_id = '$edge_node_id'::uuid
      AND database_role = '$edge_role'
")" = "1"
test "$(psql_super -c "
    SELECT NOT rolcanlogin AND rolinherit AND NOT rolsuper
       AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication
       AND NOT rolbypassrls AND rolconnlimit = 1 AND rolpassword IS NULL
    FROM pg_catalog.pg_authid
    WHERE rolname = '$edge_role'
")" = "t"
test "$(psql_super -c "
    SELECT count(*) = 1 AND bool_and(
        granted.rolname = 'aurum_edge_cash_executor'
        AND NOT membership.admin_option
        AND membership.inherit_option
        AND NOT membership.set_option
    )
    FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS granted ON granted.oid = membership.roleid
    JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
    WHERE member.rolname = '$edge_role'
")" = "t"
test "$(psql_super -c "
    SELECT count(*)
    FROM pg_catalog.pg_database AS databases
    CROSS JOIN LATERAL pg_catalog.aclexplode(databases.datacl) AS acl
    JOIN pg_catalog.pg_roles AS grantees ON grantees.oid = acl.grantee
    WHERE grantees.rolname = '$edge_role'
")" = "0"

psql_super -v edge_role="$edge_role" -v database_name="$POSTGRES_DB" <<'SQL'
GRANT aurum_support TO :"edge_role";
GRANT CONNECT ON DATABASE :"database_name" TO :"edge_role";
GRANT CONNECT ON DATABASE postgres TO :"edge_role";
GRANT USAGE ON SCHEMA public TO :"edge_role";
GRANT SELECT ON TABLE public.tenant TO :"edge_role";
SQL
run_role_bootstrap

test "$(psql_super -c "
    SELECT count(*)
    FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
    WHERE member.rolname = '$edge_role'
")" = "1"
# Schema USAGE is inherited from the executor solely to call the dispatcher;
# the device role must not retain any direct schema or table grant.
test "$(psql_super -c "
    SELECT (
        SELECT count(*)
        FROM pg_catalog.pg_database AS databases
        CROSS JOIN LATERAL pg_catalog.aclexplode(databases.datacl) AS acl
        WHERE acl.grantee = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = '$edge_role')
    ) = 0
    AND pg_catalog.has_schema_privilege('$edge_role', 'public', 'USAGE')
    AND NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS schemas
        CROSS JOIN LATERAL pg_catalog.aclexplode(schemas.nspacl) AS acl
        WHERE schemas.nspname = 'public'
          AND acl.grantee = (
            SELECT oid FROM pg_catalog.pg_roles WHERE rolname = '$edge_role'
          )
    )
    AND NOT pg_catalog.has_table_privilege('$edge_role', 'public.tenant', 'SELECT')
")" = "t"

rogue_role="aurum_edge_node_$(psql_super -c \
    "SELECT replace(gen_random_uuid()::text, '-', '')")"
psql_super -v rogue_role="$rogue_role" -v database_name="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"rogue_role" WITH LOGIN PASSWORD 'unsafe-test-password';
GRANT aurum_edge_cash_executor TO :"rogue_role";
GRANT CONNECT ON DATABASE :"database_name" TO :"rogue_role";
SQL
run_role_bootstrap
test "$(psql_super -c "
    SELECT NOT rolcanlogin AND rolpassword IS NULL
    FROM pg_catalog.pg_authid
    WHERE rolname = '$rogue_role'
")" = "t"
test "$(psql_super -c "
    SELECT count(*)
    FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
    WHERE member.rolname = '$rogue_role'
")" = "0"
test "$(psql_super -c "
    SELECT pg_catalog.has_database_privilege('$rogue_role', current_database(), 'CONNECT')
")" = "f"

PGAPPNAME=edge_identity_status_tx psql_super -c "
    BEGIN;
    UPDATE public.sync_node SET status = 'revoked'
    WHERE id = '$edge_node_id'::uuid;
    SELECT pg_sleep(3);
    COMMIT;
" &
revoke_status_pid=$!

status_lock_seen=false
attempt=0
while [ "$attempt" -lt 50 ]; do
    if [ "$(psql_super -c "
        SELECT count(*)
        FROM pg_catalog.pg_stat_activity
        WHERE application_name = 'edge_identity_status_tx'
          AND state = 'active'
    ")" = "1" ]; then
        status_lock_seen=true
        break
    fi
    attempt=$((attempt + 1))
    sleep 0.1
done
[ "$status_lock_seen" = "true" ] || {
    echo "Failed to observe the concurrent Edge revocation transaction" >&2
    exit 1
}

PGAPPNAME=edge_identity_bootstrap run_role_bootstrap &
concurrent_bootstrap_pid=$!

bootstrap_lock_seen=false
attempt=0
while [ "$attempt" -lt 50 ]; do
    if [ "$(psql_super -c "
        SELECT count(*)
        FROM pg_catalog.pg_stat_activity
        WHERE application_name = 'edge_identity_bootstrap'
          AND wait_event_type = 'Lock'
    ")" -ge 1 ]; then
        bootstrap_lock_seen=true
        break
    fi
    attempt=$((attempt + 1))
    sleep 0.1
done
[ "$bootstrap_lock_seen" = "true" ] || {
    echo "Role bootstrap did not wait for the concurrent Edge revocation" >&2
    exit 1
}

wait "$revoke_status_pid"
wait "$concurrent_bootstrap_pid"
test "$(psql_super -c "
    SELECT NOT rolcanlogin AND rolpassword IS NULL
    FROM pg_catalog.pg_authid
    WHERE rolname = '$edge_role'
")" = "t"
test "$(psql_super -c "
    SELECT count(*)
    FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
    WHERE member.rolname = '$edge_role'
")" = "0"

psql_super -v edge_role="$edge_role" -v rogue_role="$rogue_role" <<'SQL'
GRANT aurum_support TO :"edge_role";
GRANT :"edge_role" TO :"rogue_role";
SQL
run_identity_action revoke
run_identity_action revoke
test "$(psql_super -c "
    SELECT NOT rolcanlogin AND rolpassword IS NULL
    FROM pg_catalog.pg_authid
    WHERE rolname = '$edge_role'
")" = "t"
test "$(psql_super -c "
    SELECT count(*)
    FROM pg_catalog.pg_auth_members AS membership
    JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
    JOIN pg_catalog.pg_roles AS granted ON granted.oid = membership.roleid
    WHERE member.rolname = '$edge_role'
       OR granted.rolname = '$edge_role'
")" = "0"

if run_identity_action enroll; then
    echo "Enrollment accepted a revoked Edge node" >&2
    exit 1
fi

echo "Inactive Edge database identity lifecycle checks passed."
unset PGPASSWORD
