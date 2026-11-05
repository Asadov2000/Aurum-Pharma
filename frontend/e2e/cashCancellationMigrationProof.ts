import { spawnSync, type SpawnSyncReturns } from "node:child_process";
import { fileURLToPath } from "node:url";

import { assertDockerAvailable, E2E_POSTGRES_CONTAINER, E2E_POSTGRES_DB } from "./docker";
import { expect } from "./helpers";

const fingerprintSql = `SELECT json_build_object(
  'revision',(SELECT version_num FROM public.alembic_version),
  'history',(SELECT count(*) FROM public.pos_physical_cash_cancellation),
  'relations',(SELECT md5(string_agg(concat(oid,':',relowner,':',relacl,':',
    relrowsecurity,':',relforcerowsecurity),'|' ORDER BY oid)) FROM pg_class
    WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p','v')),
  'routines',(SELECT md5(string_agg(concat(oid,':',proowner,':',proacl,':',proconfig,':',
    pg_get_functiondef(oid)),'|' ORDER BY oid)) FROM pg_proc
    WHERE pronamespace='public'::regnamespace AND prokind='f'),
  'policies',(SELECT md5(string_agg(concat(polrelid,':',polname,':',polroles,':',
    pg_get_expr(polqual,polrelid),':',pg_get_expr(polwithcheck,polrelid)),
    '|' ORDER BY polrelid,polname)) FROM pg_policy),
  'triggers',(SELECT md5(string_agg(pg_get_triggerdef(oid),'|' ORDER BY oid))
    FROM pg_trigger WHERE tgrelid IN (SELECT oid FROM pg_class
      WHERE relnamespace='public'::regnamespace))
);`;

function run(args: string[], input?: Buffer): SpawnSyncReturns<Buffer> {
  const result = spawnSync("docker", args, {
    input,
    timeout: 45_000,
    maxBuffer: 64 * 1024 * 1024,
    stdio: "pipe",
  });
  // Never attach child output: a database backup or migration diagnostics can contain data.
  if (result.error || result.signal) throw new Error("Disposable migration proof did not finish");
  return result;
}

function successful(args: string[], input?: Buffer): Buffer {
  const result = run(args, input);
  expect(result.status === 0).toBe(true);
  return result.stdout;
}

function fingerprint(database: string): string {
  return successful([
    "exec",
    E2E_POSTGRES_CONTAINER,
    "psql",
    "-U",
    "postgres",
    "-d",
    database,
    "-v",
    "ON_ERROR_STOP=1",
    "-At",
    "-c",
    fingerprintSql,
  ])
    .toString("utf8")
    .trim();
}

function cloneOid(clone: string): string {
  const oid = successful([
    "exec",
    E2E_POSTGRES_CONTAINER,
    "psql",
    "-U",
    "postgres",
    "-d",
    E2E_POSTGRES_DB,
    "-v",
    "ON_ERROR_STOP=1",
    "-At",
    "-c",
    `SELECT oid FROM pg_catalog.pg_database WHERE datname='${clone}'`,
  ])
    .toString("utf8")
    .trim();
  expect(/^[1-9][0-9]*$/.test(oid)).toBe(true);
  return oid;
}

/** Run the real downgrade only on a newly created *_test clone, never on the E2E source. */
export function assertCancellationHistoryProtected(): void {
  assertDockerAvailable();
  expect(/^[a-z][a-z0-9_]{0,62}$/.test(E2E_POSTGRES_DB)).toBe(true);
  expect(["postgres", "template0", "template1"].includes(E2E_POSTGRES_DB)).toBe(false);
  // This proof uses the normal E2E source, so even a misrouted runner cannot downgrade it.
  expect(E2E_POSTGRES_DB.endsWith("_test")).toBe(false);
  const project = successful([
    "inspect",
    "--format",
    '{{ index .Config.Labels "com.docker.compose.project" }}',
    E2E_POSTGRES_CONTAINER,
  ])
    .toString("utf8")
    .trim();
  expect(/^aurum-e2e-[a-z0-9-]+$/.test(project)).toBe(true);
  const sourceBefore = fingerprint(E2E_POSTGRES_DB);
  const sourceFacts = JSON.parse(sourceBefore) as { revision: string; history: number };
  expect(sourceFacts.revision).toBe("0237");
  expect(sourceFacts.history).toBeGreaterThan(0);
  const clone = `aurum_cash_cancel_${crypto.randomUUID().replaceAll("-", "")}_test`;
  expect(/^aurum_cash_cancel_[a-f0-9]{32}_test$/.test(clone)).toBe(true);
  expect(clone === E2E_POSTGRES_DB).toBe(false);
  let created = false;
  let createdOid: string | null = null;
  try {
    successful([
      "exec",
      E2E_POSTGRES_CONTAINER,
      "createdb",
      "-U",
      "postgres",
      "-T",
      "template0",
      clone,
    ]);
    created = true;
    createdOid = cloneOid(clone);
    // pg_dump reads a consistent snapshot without terminating the source's connections.
    const dump = successful([
      "exec",
      E2E_POSTGRES_CONTAINER,
      "pg_dump",
      "-U",
      "postgres",
      "-Fc",
      E2E_POSTGRES_DB,
    ]);
    successful(
      [
        "exec",
        "-i",
        E2E_POSTGRES_CONTAINER,
        "pg_restore",
        "-U",
        "postgres",
        "--clean",
        "--if-exists",
        "--exit-on-error",
        "--dbname",
        clone,
      ],
      dump,
    );
    const cloneBefore = fingerprint(clone);
    const compose = fileURLToPath(new URL("../../docker-compose.e2e.yml", import.meta.url));
    const refused = spawnSync(
      "docker",
      [
        "compose",
        "--project-name",
        project,
        "--file",
        compose,
        "run",
        "--rm",
        "--no-deps",
        "migrate",
        "python",
        "-m",
        "app.migrate",
        "downgrade",
        "0236",
        "--allow-test-downgrade",
      ],
      {
        env: { ...process.env, AURUM_E2E_DATABASE: clone },
        timeout: 45_000,
        maxBuffer: 1024 * 1024,
        stdio: "pipe",
      },
    );
    expect(
      !refused.error && !refused.signal && refused.status !== null && refused.status !== 0,
    ).toBe(true);
    const diagnostic = Buffer.concat([refused.stdout, refused.stderr]).toString("utf8");
    expect(diagnostic.includes("physical_cash_cancellation_downgrade_requires_empty_history")).toBe(
      true,
    );
    expect(fingerprint(clone) === cloneBefore).toBe(true);
  } finally {
    // Delete only the exact database successfully created by this invocation.
    try {
      if (created && createdOid !== null) {
        expect(/^aurum_cash_cancel_[a-f0-9]{32}_test$/.test(clone)).toBe(true);
        expect(clone === E2E_POSTGRES_DB).toBe(false);
        expect(cloneOid(clone) === createdOid).toBe(true);
        successful(["exec", E2E_POSTGRES_CONTAINER, "dropdb", "-U", "postgres", "--force", clone]);
      }
    } finally {
      expect(fingerprint(E2E_POSTGRES_DB) === sourceBefore).toBe(true);
    }
  }
}
