// One-time setup for the disposable E2E database.
//
// The disposable seed owns creation of the protected owner relationship.
// This host-only setup validates that invariant before the browser suite runs.

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
    DELETE FROM email_code
    WHERE email_lower IN ('dev@aurum.tj', 'owner@aurum.tj')
      AND created_at > now() - interval '1 minute';

    SELECT u.home_tenant_id
    FROM app_user u
    JOIN tenant_membership membership
      ON membership.tenant_id = u.home_tenant_id
     AND membership.user_id = u.id
     AND membership.status = 'active'
    JOIN tenant_ownership ownership
      ON ownership.tenant_id = u.home_tenant_id
     AND ownership.membership_id = membership.id
     AND ownership.is_active
    JOIN user_assignment assignment
      ON assignment.tenant_id = u.home_tenant_id
     AND assignment.membership_id = membership.id
     AND assignment.user_id = u.id
     AND assignment.branch_id IS NULL
     AND assignment.is_active
    JOIN role r
      ON r.id = assignment.role_id
     AND r.tenant_id = u.home_tenant_id
     AND r.protected_kind = 'tenant_owner'
     AND r.is_protected = true
     AND r.is_system = false
    WHERE lower(u.email) = 'owner@aurum.tj'
      AND u.home_tenant_id IS NOT NULL
    LIMIT 1;
  `);
  if (!tenantId) {
    throw new Error("Demo Pharmacy tenant missing - rerun the isolated seed");
  }
  process.env.E2E_TENANT_ID = tenantId;

  // Start every run without stale effective-permission cache entries.
  try {
    dockerExec(E2E_REDIS_CONTAINER, ["redis-cli", "FLUSHDB"]);
  } catch {
    // The suite will report the unavailable Redis service on its first request.
  }
}
