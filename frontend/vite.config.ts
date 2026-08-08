/// <reference types="vitest" />
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const coreRuntimePackages = new Set([
  "@tanstack/history",
  "@tanstack/query-core",
  "@tanstack/react-query",
  "@tanstack/react-router",
  "@tanstack/react-store",
  "@tanstack/router-core",
  "@tanstack/store",
  "axios",
  "clsx",
  "react",
  "react-dom",
  "scheduler",
  "tailwind-merge",
  "use-sync-external-store",
  "zustand",
]);
const formPackages = new Set(["react-hook-form", "zod"]);

function packageNameFromModuleId(id: string): string | undefined {
  const normalizedId = id.replaceAll("\\", "/");
  const packagePath = normalizedId.split("/node_modules/").at(-1);
  if (!packagePath) return undefined;

  const [firstSegment, secondSegment] = packagePath.split("/");
  if (!firstSegment) return undefined;
  return firstSegment.startsWith("@") && secondSegment
    ? `${firstSegment}/${secondSegment}`
    : firstSegment;
}

function splitRuntimeDependencies(id: string): string | undefined {
  const packageName = packageNameFromModuleId(id);
  if (!packageName) return undefined;
  if (formPackages.has(packageName)) return "vendor-forms";
  if (coreRuntimePackages.has(packageName)) return "vendor-core";
  return undefined;
}

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    ...(mode === "test"
      ? {}
      : {
          fs: {
            deny: [
              "**/.env*",
              "**/e2e/**",
              "**/playwright-report/**",
              "**/test-results/**",
              "**/tests/**",
            ],
          },
        }),
    watch: {
      usePolling: true,
      interval: 1_000,
      ignored: [
        "**/node_modules/**",
        "**/dist/**",
        "**/coverage/**",
        "**/e2e/**",
        "**/playwright-report/**",
        "**/test-results/**",
        "**/tests/**",
      ],
    },
    warmup: {
      clientFiles: [
        "./src/main.tsx",
        "./src/router.tsx",
        "./src/features/auth/LoginPage.tsx",
        "./src/features/**/*Page.tsx",
      ],
    },
  },
  build: {
    manifest: true,
    rollupOptions: {
      output: {
        manualChunks: splitRuntimeDependencies,
      },
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
}));
