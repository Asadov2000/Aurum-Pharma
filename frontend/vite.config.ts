/// <reference types="vitest" />
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    watch: {
      usePolling: true,
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    // Unit tests live under tests/. e2e/*.spec.ts are Playwright specs that
    // import @playwright/test and throw if collected here — they run via
    // `pnpm e2e`, so keep vitest scoped to tests/.
    include: ["tests/**/*.{test,spec}.{ts,tsx}"],
    // Forked workers exit cleanly on Docker Desktop + Windows. The default
    // threads pool can leave Vitest hanging after all tests finish.
    pool: "forks",
    // Run test files sequentially. On this dev machine (Docker Desktop +
    // Windows) parallel workers starve each other and Testing Library's
    // findBy* queries time out non-deterministically. Sequential is slower
    // but deterministic; the suite is small.
    fileParallelism: false,
    // Generous per-test timeout — the same overloaded environment makes a
    // few interaction-heavy tests brush past the 5s default. A genuinely
    // hung test still fails at 15s.
    testTimeout: 15_000,
  },
});
