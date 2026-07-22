import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
} from "@tanstack/react-router";
import { z } from "zod";

import { RootLayout } from "@/components/layout/RootLayout";
import { LoginPage } from "@/features/auth/LoginPage";

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
const UsersPage = lazyRouteComponent(() => import("@/features/roles/UsersPage"), "UsersPage");
const RolesPage = lazyRouteComponent(() => import("@/features/roles/RolesPage"), "RolesPage");
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
const SalesPage = lazyRouteComponent(() => import("@/features/sales/SalesPage"), "SalesPage");
const BillingPage = lazyRouteComponent(
  () => import("@/features/billing/BillingPage"),
  "BillingPage",
);
const AuditPage = lazyRouteComponent(() => import("@/features/audit/AuditPage"), "AuditPage");
const OnboardingPage = lazyRouteComponent(
  () => import("@/features/onboarding/OnboardingPage"),
  "OnboardingPage",
);
const NotificationsPage = lazyRouteComponent(
  () => import("@/features/notifications/NotificationsPage"),
  "NotificationsPage",
);
const SecurityPage = lazyRouteComponent(
  () => import("@/features/auth/SecurityPage"),
  "SecurityPage",
);
const ReportsPage = lazyRouteComponent(
  () => import("@/features/reports/ReportsPage"),
  "ReportsPage",
);

const rootRoute = createRootRoute({
  component: RootLayout,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: DashboardPage,
});

const tenantsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/tenants",
  component: TenantsPage,
});

const branchesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/branches",
  component: BranchesPage,
});

const registersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/registers",
  component: RegistersPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsPage,
});

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/users",
  component: UsersPage,
});

const rolesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/roles",
  component: RolesPage,
});

const catalogRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/catalog",
  component: CatalogPage,
});

const batchesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/batches",
  component: BatchesPage,
});

const suppliersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/suppliers",
  component: SuppliersPage,
});

const incomingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/incoming",
  component: IncomingPage,
});

const incomingDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/incoming/$id",
  component: IncomingDetailPage,
});

const posRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/pos",
  component: POSPage,
});

const salesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sales",
  component: SalesPage,
});

const billingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/billing",
  component: BillingPage,
});

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  component: AuditPage,
});

const onboardingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/onboarding",
  component: OnboardingPage,
});

const notificationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/notifications",
  component: NotificationsPage,
});

const securityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/security",
  component: SecurityPage,
});

const reportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/reports",
  component: ReportsPage,
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
  securityRoute,
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
