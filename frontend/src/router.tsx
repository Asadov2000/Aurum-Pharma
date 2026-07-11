import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  type RouteComponent,
} from "@tanstack/react-router";
import { Suspense, type ReactNode } from "react";
import { z } from "zod";

import { AppLayout } from "@/components/layout/AppLayout";
import { AuthGuard } from "@/features/auth/AuthGuard";
import { LoginPage } from "@/features/auth/LoginPage";

const routePending = (
  <div
    role="status"
    aria-live="polite"
    className="flex min-h-48 items-center justify-center text-sm text-foreground-muted"
  >
    Загрузка…
  </div>
);

const DashboardPage = lazyRouteComponent(
  () => import("@/features/dashboard/DashboardPage"),
  "DashboardPage",
);
const TenantsPage = lazyRouteComponent(
  () => import("@/features/foundation/TenantsPage"),
  "TenantsPage",
);
const BranchesPage = lazyRouteComponent(
  () => import("@/features/foundation/BranchesPage"),
  "BranchesPage",
);
const RegistersPage = lazyRouteComponent(
  () => import("@/features/foundation/RegistersPage"),
  "RegistersPage",
);
const SettingsPage = lazyRouteComponent(
  () => import("@/features/foundation/SettingsPage"),
  "SettingsPage",
);
const UsersPage = lazyRouteComponent(
  () => import("@/features/roles/UsersPage"),
  "UsersPage",
);
const RolesPage = lazyRouteComponent(
  () => import("@/features/roles/RolesPage"),
  "RolesPage",
);
const CatalogPage = lazyRouteComponent(
  () => import("@/features/catalog/CatalogPage"),
  "CatalogPage",
);
const BatchesPage = lazyRouteComponent(
  () => import("@/features/inventory/BatchesPage"),
  "BatchesPage",
);
const SuppliersPage = lazyRouteComponent(
  () => import("@/features/suppliers/SuppliersPage"),
  "SuppliersPage",
);
const IncomingPage = lazyRouteComponent(
  () => import("@/features/incoming/IncomingPage"),
  "IncomingPage",
);
const IncomingDetailPage = lazyRouteComponent(
  () => import("@/features/incoming/IncomingDetailPage"),
  "IncomingDetailPage",
);
const POSPage = lazyRouteComponent(() => import("@/features/pos/POSPage"), "POSPage");
const SalesPage = lazyRouteComponent(
  () => import("@/features/sales/SalesPage"),
  "SalesPage",
);
const BillingPage = lazyRouteComponent(
  () => import("@/features/billing/BillingPage"),
  "BillingPage",
);
const AuditPage = lazyRouteComponent(
  () => import("@/features/audit/AuditPage"),
  "AuditPage",
);
const OnboardingPage = lazyRouteComponent(
  () => import("@/features/onboarding/OnboardingPage"),
  "OnboardingPage",
);
const NotificationsPage = lazyRouteComponent(
  () => import("@/features/notifications/NotificationsPage"),
  "NotificationsPage",
);
const ReportsPage = lazyRouteComponent(
  () => import("@/features/reports/ReportsPage"),
  "ReportsPage",
);

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

function protectedPage(Page: RouteComponent): RouteComponent {
  const ProtectedPage = () =>
    protect(
      <Suspense fallback={routePending}>
        <Page />
      </Suspense>,
    );
  ProtectedPage.preload = Page.preload;
  return ProtectedPage;
}

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: protectedPage(DashboardPage),
});

const tenantsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/tenants",
  component: protectedPage(TenantsPage),
});

const branchesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/branches",
  component: protectedPage(BranchesPage),
});

const registersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/registers",
  component: protectedPage(RegistersPage),
});

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: protectedPage(SettingsPage),
});

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/users",
  component: protectedPage(UsersPage),
});

const rolesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/roles",
  component: protectedPage(RolesPage),
});

const catalogRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/catalog",
  component: protectedPage(CatalogPage),
});

const batchesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/batches",
  component: protectedPage(BatchesPage),
});

const suppliersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/suppliers",
  component: protectedPage(SuppliersPage),
});

const incomingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/incoming",
  component: protectedPage(IncomingPage),
});

const incomingDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/incoming/$id",
  component: protectedPage(IncomingDetailPage),
});

const posRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/pos",
  component: protectedPage(POSPage),
});

const salesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sales",
  component: protectedPage(SalesPage),
});

const billingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/billing",
  component: protectedPage(BillingPage),
});

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  component: protectedPage(AuditPage),
});

const onboardingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/onboarding",
  component: protectedPage(OnboardingPage),
});

const notificationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/notifications",
  component: protectedPage(NotificationsPage),
});

const reportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/reports",
  component: protectedPage(ReportsPage),
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
  salesRoute,
  billingRoute,
  auditRoute,
  onboardingRoute,
  notificationsRoute,
  reportsRoute,
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
