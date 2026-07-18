// One-time setup for the disposable E2E database.
//
// The owner assignment cannot be created through the support API because
// support users deliberately have no home tenant. This host-only setup uses
// the disposable Postgres container and never runs against production.

import {
  assertDockerAvailable,
  dockerExec,
  E2E_POSTGRES_CONTAINER,
  E2E_POSTGRES_DB,
  E2E_REDIS_CONTAINER,
} from "./docker";

function psql(sql: string): string {
  return dockerExec(E2E_POSTGRES_CONTAINER, [
    "psql",
    "-U",
    "postgres",
    "-d",
    E2E_POSTGRES_DB,
    "-qAt",
    "-c",
    sql,
  ]).trim();
}

export default async function globalSetup(): Promise<void> {
  assertDockerAvailable();

  // Keep database preparation in one Docker call. Docker Desktop on Windows
  // can become unstable when the suite shells out repeatedly during startup.
  const tenantId = psql(`
    SET SESSION AUTHORIZATION aurum_support;
    SET app.support_session = 'true';

    INSERT INTO user_assignment (user_id, tenant_id, role_id, is_active, password_required)
    SELECT u.id, u.home_tenant_id, r.id, true, false
    FROM app_user u, role r
    WHERE lower(u.email) = 'owner@aurum.tj'
      AND r.protected_kind = 'tenant_owner'
      AND r.is_protected = true
      AND r.is_system = false
      AND r.tenant_id = u.home_tenant_id
      AND u.home_tenant_id IS NOT NULL
    ON CONFLICT (user_id, tenant_id, branch_id)
      DO UPDATE SET is_active = true, role_id = EXCLUDED.role_id;

    RESET app.support_session;
    RESET SESSION AUTHORIZATION;

    DELETE FROM email_code
    WHERE email_lower IN ('dev@aurum.tj', 'owner@aurum.tj')
      AND created_at > now() - interval '1 minute';

    SELECT home_tenant_id
    FROM app_user
    WHERE lower(email) = 'owner@aurum.tj'
    LIMIT 1;
  `);
  if (!tenantId) {
    throw new Error("Demo Pharmacy tenant missing - rerun the isolated seed");
  }
  process.env.E2E_TENANT_ID = tenantId;

  // Drop effective-permission cache after repairing the owner assignment.
  try {
    dockerExec(E2E_REDIS_CONTAINER, ["redis-cli", "FLUSHDB"]);
  } catch {
    // The suite will report the unavailable Redis service on its first request.
  }
}
