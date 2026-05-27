// One-time global setup. Two responsibilities:
//
// 1. Make sure owner@aurum.tj has the system "owner" role assigned in Demo
//    Pharmacy. Without that user_assignment, every tenant-scoped POST that
//    `require_permission` guards returns 403, and most specs collapse.
//
//    We could not do this through the API in phase 1: assigning a role
//    needs caller.tenant_id, which neither dev nor admin have (they are
//    support users with no home_tenant_id). So we shell out to psql.
//    This is intentionally a thin escape hatch — only here, never inside
//    individual specs.
//
// 2. Resolve and cache the Demo tenant UUID for downstream specs via
//    process.env.E2E_TENANT_ID.

import { execSync } from "node:child_process";

const PSQL_PREFIX = `docker exec aurum-postgres psql -U postgres -d aurum -At -c`;

function psql(sql: string): string {
  return execSync(`${PSQL_PREFIX} "${sql.replace(/"/g, '\\"')}"`, {
    encoding: "utf8",
  }).trim();
}

export default async function globalSetup(): Promise<void> {
  // (1) Ensure owner has an active owner-role assignment.
  psql(`
    INSERT INTO user_assignment (user_id, tenant_id, role_id, is_active, password_required)
    SELECT u.id, u.home_tenant_id, r.id, true, false
    FROM app_user u, role r
    WHERE u.email = 'owner@aurum.tj'
      AND r.name = 'owner'
      AND r.is_system = true
      AND u.home_tenant_id IS NOT NULL
    ON CONFLICT (user_id, tenant_id, branch_id) DO UPDATE SET is_active = true
  `);

  // (2) Cache Demo Pharmacy id for specs that need cross-process seed data.
  const tenantId = psql(
    "SELECT id FROM tenant WHERE name = 'Demo Pharmacy' LIMIT 1",
  );
  if (!tenantId) {
    throw new Error("Demo Pharmacy tenant missing — re-run the initial seed");
  }
  process.env.E2E_TENANT_ID = tenantId;

  // (3) Drop the effective-permission Redis cache (`auth:perms:*`).
  // The backend caches permissions for 5 minutes; without flushing here,
  // owner could log in to a STALE empty cache from a previous session
  // (before we created the user_assignment in step 1) and 403 on every
  // tenant-scoped write.
  try {
    execSync(`docker exec aurum-redis redis-cli FLUSHDB`, { encoding: "utf8" });
  } catch {
    // Redis container not running — most specs will fail later anyway.
  }

  // (4) Wipe any lingering per-minute login-code rate-limit for our two
  // test users — flaky CI otherwise. Aggregate hour-bucket survives.
  psql(
    "DELETE FROM email_code WHERE email_lower IN ('dev@aurum.tj','owner@aurum.tj') AND created_at > now() - interval '1 minute'",
  );
}
