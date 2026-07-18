import { execFileSync, spawnSync } from "node:child_process";

export const E2E_POSTGRES_CONTAINER =
  process.env.E2E_POSTGRES_CONTAINER ?? "aurum-postgres";
export const E2E_REDIS_CONTAINER = process.env.E2E_REDIS_CONTAINER ?? "aurum-redis";
export const E2E_POSTGRES_DB = process.env.E2E_POSTGRES_DB ?? "aurum";

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
}

export function dockerExec(container: string, args: string[]): string {
  const attempts = 2;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return execFileSync("docker", ["exec", container, ...args], {
        encoding: "utf8",
        timeout: 45_000,
      });
    } catch (error) {
      if (isErrno(error) && error.code === "ENOENT") {
        throw new Error(hostOnlyMessage);
      }
      if (!isTransientDockerError(error) || attempt === attempts) {
        throw error;
      }
      sleepSync(500 * attempt);
    }
  }
  throw new Error("Docker command failed without an error");
}
