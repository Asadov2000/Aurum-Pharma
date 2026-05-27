import { defineConfig, devices } from "@playwright/test";

// Run against the real docker-compose stack: frontend on 5173, backend on 8000.
// Both must be up before `pnpm e2e` — see docs/e2e.md.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Sequential by default — POS/inventory tests assert global DB state.
  workers: 1,
  fullyParallel: false,
  // One retry absorbs transient infra hiccups (Docker Desktop pipe drops,
  // a cold Vite chunk) without masking real product bugs — a genuinely
  // broken flow fails both attempts.
  retries: 1,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:5173",
    // Backend API base; helpers use it directly for seed.
    extraHTTPHeaders: {},
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
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
