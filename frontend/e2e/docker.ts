import { execFileSync, spawnSync } from "node:child_process";

export const E2E_POSTGRES_CONTAINER = process.env.E2E_POSTGRES_CONTAINER ?? "";
export const E2E_REDIS_CONTAINER = process.env.E2E_REDIS_CONTAINER ?? "";
export const E2E_POSTGRES_DB = process.env.E2E_POSTGRES_DB ?? "aurum";

const isolatedOnlyMessage =
  "E2E data preparation requires Postgres and Redis from the same disposable " +
  "aurum-e2e-* Compose project. Run: pnpm e2e:isolated";
let isolatedContainersVerified = false;

function assertIsolatedContainers(): void {
  if (isolatedContainersVerified) return;
  // Immutable IDs make this per-process verification safe if a named container
  // is recreated while tests run. Both launchers obtain IDs via Compose ps -q.
  const containerId = /^[a-f0-9]{12,64}$/;
  if (!containerId.test(E2E_POSTGRES_CONTAINER) || !containerId.test(E2E_REDIS_CONTAINER)) {
    throw new Error(isolatedOnlyMessage);
  }
  let labels: string[];
  try {
    // Inspect only Compose labels, never container environment or credentials.
    labels = execFileSync(
      "docker",
      [
        "inspect",
        "--format",
        '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}',
        E2E_POSTGRES_CONTAINER,
        E2E_REDIS_CONTAINER,
      ],
      { encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "pipe"] },
    )
      .trim()
      .split(/\r?\n/);
  } catch {
    throw new Error(isolatedOnlyMessage);
  }
  const [postgresProject, postgresService] = (labels[0] ?? "").split("|");
  const [redisProject, redisService] = (labels[1] ?? "").split("|");
  if (
    labels.length !== 2 ||
    !/^aurum-e2e-[a-z0-9-]+$/.test(postgresProject ?? "") ||
    postgresProject !== redisProject ||
    postgresService !== "postgres" ||
    redisService !== "redis"
  ) {
    throw new Error(isolatedOnlyMessage);
  }
  isolatedContainersVerified = true;
}

const hostOnlyMessage = [
  "E2E tests must be started from the host machine, not from the frontend container.",
  "Run: cd frontend && pnpm e2e:isolated",
  "Reason: global setup seeds Docker Postgres/Redis containers with `docker exec`,",
  "and the frontend Alpine container intentionally does not include Docker CLI.",
].join("\n");

function isErrno(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}

function errorDetails(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  const stderr = "stderr" in error ? String(error.stderr) : "";
  return `${error.message}\n${stderr}`;
}

function isTransientDockerError(error: unknown): boolean {
  return /500 Internal Server Error|dockerDesktopLinuxEngine|context deadline exceeded|ETIMEDOUT|timed out/i.test(
    errorDetails(error),
  );
}

function sleepSync(milliseconds: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

export function assertDockerAvailable(): void {
  const probe = spawnSync("docker", ["version"], { stdio: "ignore" });
  if (probe.error || probe.status !== 0) {
    throw new Error(hostOnlyMessage);
  }
  assertIsolatedContainers();
}

export function dockerExec(container: string, args: string[]): string {
  assertIsolatedContainers();
  if (container !== E2E_POSTGRES_CONTAINER && container !== E2E_REDIS_CONTAINER) {
    throw new Error(isolatedOnlyMessage);
  }
  const attempts = 2;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return execFileSync("docker", ["exec", container, ...args], {
        encoding: "utf8",
        timeout: 45_000,
      });
    } catch (error) {
      if (isErrno(error) && error.code === "ENOENT") {
        throw new Error(hostOnlyMessage, { cause: error });
      }
      if (!isTransientDockerError(error) || attempt === attempts) {
        throw error;
      }
      sleepSync(500 * attempt);
    }
  }
  throw new Error("Docker command failed without an error");
}
