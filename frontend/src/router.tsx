import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import { type ReactNode } from "react";
import { z } from "zod";

import { AppLayout } from "@/components/layout/AppLayout";
import { AuthGuard } from "@/features/auth/AuthGuard";
import { Dashboard } from "@/features/auth/Dashboard";
import { LoginPage } from "@/features/auth/LoginPage";
import { CatalogPage } from "@/features/catalog/CatalogPage";
import { IncomingDetailPage } from "@/features/incoming/IncomingDetailPage";
import { IncomingPage } from "@/features/incoming/IncomingPage";
import { BatchesPage } from "@/features/inventory/BatchesPage";
import { BranchesPage } from "@/features/foundation/BranchesPage";
import { RegistersPage } from "@/features/foundation/RegistersPage";
import { SettingsPage } from "@/features/foundation/SettingsPage";
import { TenantsPage } from "@/features/foundation/TenantsPage";
import { RolesPage } from "@/features/roles/RolesPage";
import { AuditPage } from "@/features/audit/AuditPage";
import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import { OnboardingPage } from "@/features/onboarding/OnboardingPage";
import { BillingPage } from "@/features/billing/BillingPage";
import { POSPage } from "@/features/pos/POSPage";
import { UsersPage } from "@/features/roles/UsersPage";
import { SuppliersPage } from "@/features/suppliers/SuppliersPage";

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

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/users",
  component: () => protect(<UsersPage />),
});

const rolesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/roles",
  component: () => protect(<RolesPage />),
});

const catalogRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/catalog",
  component: () => protect(<CatalogPage />),
});

const batchesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/batches",
  component: () => protect(<BatchesPage />),
});

const suppliersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/suppliers",
  component: () => protect(<SuppliersPage />),
});

const incomingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/incoming",
  component: () => protect(<IncomingPage />),
});

const incomingDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/incoming/$id",
  component: () => protect(<IncomingDetailPage />),
});

const posRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/pos",
  component: () => protect(<POSPage />),
});

const billingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/billing",
  component: () => protect(<BillingPage />),
});

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  component: () => protect(<AuditPage />),
});

const onboardingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/onboarding",
  component: () => protect(<OnboardingPage />),
});

const notificationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/notifications",
  component: () => protect(<NotificationsPage />),
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
  usersRoute,
  rolesRoute,
  catalogRoute,
  batchesRoute,
  suppliersRoute,
  incomingRoute,
  incomingDetailRoute,
  posRoute,
  billingRoute,
  auditRoute,
  onboardingRoute,
  notificationsRoute,
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
