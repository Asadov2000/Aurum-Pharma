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
// Let Zod core/mini and lazy classic schemas follow their actual import graph.
// Grouping all Zod modules with RHF eagerly loads validators for lazy forms.
const formPackages = new Set(["react-hook-form"]);

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
  const normalizedId = id.replaceAll("\\", "/");
  // Recovery is shared by the eager draft reader and lazy payment panel.
  // Keep those small primitives with the existing lazy controls, so the panel
  // cannot absorb POSPage's route entry or add a POS startup request.
  if (
    /\/src\/features\/pos\/(operationId|operationStorage|posStorageScope|physicalCashRecoveryPresence|unitPaymentPreparationPresence)\.ts$/.test(
      normalizedId,
    )
  ) {
    return "ui-controls";
  }
  // Shared controls travel with lazy pages, rather than being evaluated by
  // the UI barrel at startup. Group them to avoid extra network requests.
  if (/\/src\/components\/ui\/(Input|Label|FormError)\.tsx$/.test(normalizedId)) {
    return "ui-fields";
  }
  if (
    /\/src\/components\/ui\/(Select|Switch|Textarea|Checkbox|ActionMenu)\.tsx$/.test(normalizedId)
  ) {
    return "ui-controls";
  }
  // Record menus share ActionMenu and preference persistence. Keep their small
  // runtime helpers in its existing lazy chunk rather than adding three POS
  // startup requests; the settings form's Zod schema remains separate.
  if (
    /\/src\/features\/(foundation\/(RecordActionMenu|recordActionPresentation)\.tsx|settings\/(useQuickActionPreferences|usePreferenceAutosave|preferencesMutation|quickActions|quickActionStorage)\.ts)$/.test(
      normalizedId,
    )
  ) {
    return "ui-controls";
  }
  if (/\/src\/components\/ui\/(Modal|ConfirmDialog|Table)\.tsx$/.test(normalizedId)) {
    return "ui-controls";
  }
  // These modules are already shared by Login, Dashboard and POS. Keep them
  // with the core runtime instead of emitting a request for each tiny helper.
  if (
    /\/src\/(components\/ui\/Button\.tsx|components\/layout\/routeAccess\.ts|features\/auth\/tenantContext\.ts|lib\/(utils|errorMessages)\.ts)$/.test(normalizedId)
  ) return "vendor-core";
  // Group only the measured protected shell dependencies, not whole feature
  // directories: lazy forms and their classic validators must remain separate.
  if (
    /\/src\/components\/layout\/(AuthenticatedShell|AppLayout|AppHeader|HeaderMenuContent|HeaderProfileExtras|PwaInstallButton|RuntimeSurfaceBadge|icons|Sidebar|BrandMark|RouteAccessGuard|ConnectivityStatusBanner|PwaUpdateBanner|nav)\.tsx$/.test(normalizedId) ||
    /\/src\/components\/layout\/sidebarPreferences\.ts$/.test(normalizedId) ||
    /\/src\/components\/(AccessDeniedCard|ui\/(Card|PageHeader|Skeleton|Badge))\.tsx$/.test(normalizedId) ||
    /\/src\/(lib\/bodyScrollLock|features\/auth\/(hooks|permissions)|features\/settings\/(preferenceQueueStorage|workspacePreferences|useSidebarPreferences|queries|api))\.ts$/.test(normalizedId)
  ) return "AuthenticatedShell";
  // Mini and shared core have no classic schema convenience methods. Sharing
  // their chunk with RHF avoids fragmenting auth without loading lazy schemas.
  if (/\/node_modules\/zod\/v4\/(core|mini)\//.test(normalizedId) ||
      /\/node_modules\/zod\/v4\/locales\/en\.js$/.test(normalizedId) ||
      /\/src\/features\/auth\/(identityForms|identityQueries|identityApi|accountSecurityErrors)\.ts$/.test(normalizedId) ||
      /\/src\/features\/auth\/LoginMethodSwitch\.tsx$/.test(normalizedId)) {
    return "vendor-forms";
  }
  if (/\/node_modules\/zod\/v4\/classic\//.test(normalizedId)) {
    return "vendor-validation";
  }
  // Session revocation is used by the lazy security page. QueryClient still
  // needs MutationCache/Mutation in core; only the hook/observer are optional.
  if (
    /\/node_modules\/@tanstack\/react-query\/build\/modern\/useMutation\.js$/.test(normalizedId) ||
    /\/node_modules\/@tanstack\/query-core\/build\/modern\/mutationObserver\.js$/.test(normalizedId)
  ) {
    return "query-mutations";
  }
  // React's CommonJS proxies share this Rollup helper. Keep it with them,
  // rather than creating a vendor-core -> entry -> vendor-core cycle.
  if (normalizedId === "\0commonjsHelpers.js") {
    return "vendor-core";
  }
  const packageName = packageNameFromModuleId(id);
  if (!packageName) return undefined;
  if (
    packageName === "@tanstack/react-query" &&
    normalizedId.endsWith("/build/modern/useMutationState.js")
  ) {
    return "query-mutation-state";
  }
  // Navigation blocking is used only by lazy editors. Keep that
  // optional router module out of the runtime loaded by Dashboard and POS.
  if (
    packageName === "@tanstack/react-router" &&
    id.replaceAll("\\", "/").endsWith("/dist/esm/useBlocker.js")
  ) {
    return "router-blocker";
  }
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
        // A control's shared dependencies must not pull it back into the entry.
        onlyExplicitManualChunks: true,
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
