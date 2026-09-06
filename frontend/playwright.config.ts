import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:15173";
const isCI = process.env.CI === "true";
const ciRetries = process.env.E2E_RETRIES === "0" ? 0 : 1;
const browserExecutablePath = process.env.E2E_BROWSER_EXECUTABLE_PATH;

// Defaults target disposable E2E ports. The launcher supplies container IDs;
// setup verifies their Compose project before any database or Redis mutation.
export default defineConfig({
  testDir: "./e2e",
  // Generous timeouts: the dev Vite server in Docker can take several
  // seconds per route on a loaded host, so navigation + a few interactions
  // need headroom. Real bugs still fail well inside these limits.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  // Sequential by default — POS/inventory tests assert global DB state.
  workers: 1,
  fullyParallel: false,
  // Local Docker Desktop gets two recovery attempts. CI retries once for
  // diagnostics, but failOnFlakyTests keeps a recovered test blocking.
  retries: isCI ? ciRetries : 2,
  failOnFlakyTests: isCI,
  forbidOnly: isCI,
  reporter: isCI ? "list" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    // Backend API base; helpers use it directly for seed.
    extraHTTPHeaders: {},
    actionTimeout: 15_000,
    navigationTimeout: 90_000,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  globalSetup: "./e2e/global-setup.ts",
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(browserExecutablePath
          ? { launchOptions: { executablePath: browserExecutablePath } }
          : {}),
      },
    },
  ],
});
