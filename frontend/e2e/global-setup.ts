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
    "-At",
    "-c",
    sql,
  ]).trim();
}

export default async function globalSetup(): Promise<void> {
  assertDockerAvailable();

  // (1) Ensure owner@ has an active assignment to its tenant's «Владелец» role.
  //     owner/seller were demoted from system roles to per-tenant roles
  //     (migration 0020), so we match the tenant-scoped role by name, not the
  //     old system 'owner'. role_id is corrected on conflict for safety.
  psql(`
    INSERT INTO user_assignment (user_id, tenant_id, role_id, is_active, password_required)
    SELECT u.id, u.home_tenant_id, r.id, true, false
    FROM app_user u, role r
    WHERE u.email = 'owner@aurum.tj'
      AND r.name = 'Владелец'
      AND r.is_system = false
      AND r.tenant_id = u.home_tenant_id
      AND u.home_tenant_id IS NOT NULL
    ON CONFLICT (user_id, tenant_id, branch_id)
      DO UPDATE SET is_active = true, role_id = EXCLUDED.role_id
  `);

  // (2) Cache the demo tenant id for specs that need cross-process seed data.
  // The demo seeder renames it to «Аптека Шифо»; accept either name.
  const tenantId = psql(
    "SELECT id FROM tenant WHERE name IN ('Аптека Шифо', 'Demo Pharmacy') ORDER BY name LIMIT 1",
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
    dockerExec(E2E_REDIS_CONTAINER, ["redis-cli", "FLUSHDB"]);
  } catch {
    // Redis container not running — most specs will fail later anyway.
  }

  // (4) Wipe any lingering per-minute login-code rate-limit for our two
  // test users — flaky CI otherwise. Aggregate hour-bucket survives.
  psql(
    "DELETE FROM email_code WHERE email_lower IN ('dev@aurum.tj','owner@aurum.tj') AND created_at > now() - interval '1 minute'",
  );
}
