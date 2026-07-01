import { execFileSync, spawnSync } from "node:child_process";

const hostOnlyMessage = [
  "E2E tests must be started from the host machine, not from the frontend container.",
  "Run: cd frontend && pnpm e2e",
  "Reason: global setup seeds Docker Postgres/Redis containers with `docker exec`,",
  "and the frontend Alpine container intentionally does not include Docker CLI.",
].join("\n");

function isErrno(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}

export function assertDockerAvailable(): void {
  const probe = spawnSync("docker", ["version"], { stdio: "ignore" });
  if (probe.error || probe.status !== 0) {
    throw new Error(hostOnlyMessage);
  }
}

export function dockerExec(container: string, args: string[]): string {
  try {
    return execFileSync("docker", ["exec", container, ...args], {
      encoding: "utf8",
    });
  } catch (error) {
    if (isErrno(error) && error.code === "ENOENT") {
      throw new Error(hostOnlyMessage);
    }
    throw error;
  }
}
