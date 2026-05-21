import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import { type ReactNode } from "react";
import { z } from "zod";

import { AppLayout } from "@/components/layout/AppLayout";
import { AuthGuard } from "@/features/auth/AuthGuard";
import { Dashboard } from "@/features/auth/Dashboard";
import { LoginPage } from "@/features/auth/LoginPage";
import { BranchesPage } from "@/features/foundation/BranchesPage";
import { RegistersPage } from "@/features/foundation/RegistersPage";
import { SettingsPage } from "@/features/foundation/SettingsPage";
import { TenantsPage } from "@/features/foundation/TenantsPage";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

// Single helper so every protected page gets the same shell + guard.
function protect(node: ReactNode): JSX.Element {
  return (
    <AuthGuard>
      <AppLayout>{node}</AppLayout>
    </AuthGuard>
  );
}

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: () => protect(<Dashboard />),
});

const tenantsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/tenants",
  component: () => protect(<TenantsPage />),
});

const branchesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/branches",
  component: () => protect(<BranchesPage />),
});

const registersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/registers",
  component: () => protect(<RegistersPage />),
});

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: () => protect(<SettingsPage />),
});

const loginSearchSchema = z.object({
  from: z.string().optional(),
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
  validateSearch: (raw) => loginSearchSchema.parse(raw),
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  tenantsRoute,
  branchesRoute,
  registersRoute,
  settingsRoute,
  loginRoute,
]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
