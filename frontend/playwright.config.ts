import { defineConfig, devices } from "@playwright/test";

// Run against the real docker-compose stack: frontend on 5173, backend on 8000.
// Both must be up before `pnpm e2e` — see docs/e2e.md.
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
  // Two retries absorb transient infra hiccups (Docker Desktop pipe drops,
  // slow cold Vite chunks) without masking real product bugs — a genuinely
  // broken flow fails every attempt.
  retries: 2,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:5173",
    // Backend API base; helpers use it directly for seed.
    extraHTTPHeaders: {},
    actionTimeout: 15_000,
    navigationTimeout: 45_000,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  globalSetup: "./e2e/global-setup.ts",
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
