// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const docker = vi.hoisted(() => ({ execFileSync: vi.fn(), spawnSync: vi.fn() }));
vi.mock("node:child_process", () => docker);

const postgresId = "a".repeat(64);
const redisId = "b".repeat(64);

beforeEach(() => {
  vi.resetModules();
  vi.resetAllMocks();
  vi.stubEnv("E2E_POSTGRES_CONTAINER", postgresId);
  vi.stubEnv("E2E_REDIS_CONTAINER", redisId);
  docker.spawnSync.mockReturnValue({ status: 0 });
  docker.execFileSync.mockImplementation((_command: string, args: string[]) =>
    args[0] === "inspect" ? "aurum-e2e-local|postgres\naurum-e2e-local|redis\n" : "ok",
  );
});

afterEach(() => vi.unstubAllEnvs());

function expectNoMutation(): void {
  expect(docker.execFileSync.mock.calls.filter((call) => call[1][0] === "exec")).toHaveLength(0);
}

describe("disposable E2E environment boundary", () => {
  it.each([undefined, "aurum-postgres", "aurum-demo-postgres"])(
    "refuses absent or mutable container names (%s) before Docker access",
    async (container) => {
      vi.stubEnv("E2E_POSTGRES_CONTAINER", container);
      const { dockerExec, E2E_POSTGRES_CONTAINER } = await import("../../e2e/docker");
      expect(() => dockerExec(E2E_POSTGRES_CONTAINER, ["psql"])).toThrow("disposable");
      expect(docker.execFileSync).not.toHaveBeenCalled();
    },
  );

  it.each([
    "a-pharma|postgres\na-pharma|redis",
    "aurum-demo|postgres\naurum-demo|redis",
    "aurum-e2e-local|postgres\naurum-e2e-ci|redis",
    "aurum-e2e-local|backend\naurum-e2e-local|redis",
    "aurum-e2e-local|postgres\naurum-e2e-local|backend",
    "|\n|",
    "aurum-e2e-local|postgres",
  ])("refuses mismatched project/service labels (%s)", async (labels) => {
    docker.execFileSync.mockReturnValue(labels);
    const { dockerExec } = await import("../../e2e/docker");
    expect(() => dockerExec(redisId, ["redis-cli", "FLUSHDB"])).toThrow("disposable");
    expectNoMutation();
  });

  it("fails closed without exposing Docker diagnostics if inspection fails", async () => {
    docker.execFileSync.mockImplementation(() => {
      throw new Error("private diagnostic text");
    });
    const { dockerExec } = await import("../../e2e/docker");
    expect(() => dockerExec(postgresId, ["psql"])).toThrow("pnpm e2e:isolated");
    expectNoMutation();
  });

  it.each(["aurum-e2e-local", "aurum-e2e-ci"])(
    "allows the two inspected immutable IDs from %s and inspects once",
    async (project) => {
      docker.execFileSync.mockImplementation((_command: string, args: string[]) =>
        args[0] === "inspect" ? `${project}|postgres\n${project}|redis\n` : "ok",
      );
      const { assertDockerAvailable, dockerExec } = await import("../../e2e/docker");
      assertDockerAvailable();
      expect(dockerExec(postgresId, ["psql"])).toBe("ok");
      expect(dockerExec(redisId, ["redis-cli", "PING"])).toBe("ok");
      expect(
        docker.execFileSync.mock.calls.filter((call) => call[1][0] === "inspect"),
      ).toHaveLength(1);
      expect(docker.execFileSync.mock.calls.filter((call) => call[1][0] === "exec")).toHaveLength(
        2,
      );
    },
  );

  it("refuses an unverified target even after successful verification", async () => {
    const { assertDockerAvailable, dockerExec } = await import("../../e2e/docker");
    assertDockerAvailable();
    expect(() => dockerExec("c".repeat(64), ["psql"])).toThrow("disposable");
    expectNoMutation();
  });

  it("fails before inspection when Docker is unavailable", async () => {
    docker.spawnSync.mockReturnValue({ status: 1 });
    const { assertDockerAvailable } = await import("../../e2e/docker");
    expect(assertDockerAvailable).toThrow("host machine");
    expect(docker.execFileSync).not.toHaveBeenCalled();
  });
});
