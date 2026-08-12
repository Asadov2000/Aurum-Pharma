import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
} from "@tanstack/react-router";

import { RootLayout } from "@/components/layout/RootLayout";

const DashboardPage = lazyRouteComponent(
  () => import("@/features/dashboard/DashboardPage"),
  "DashboardPage",
);
const LoginPage = lazyRouteComponent(() => import("@/features/auth/LoginPage"), "LoginPage");
const PlatformActivationPage = lazyRouteComponent(
  () => import("@/features/platformAccounts/PlatformActivationPage"),
  "PlatformActivationPage",
);
const TenantsPage = lazyRouteComponent(
  () => import("@/features/foundation/TenantsPage"),
  "TenantsPage",
);
const PlatformControlPage = lazyRouteComponent(
  () => import("@/features/platformControl/PlatformControlPage"),
  "PlatformControlPage",
);
const SyncCenterPage = lazyRouteComponent(
  () => import("@/features/syncCenter/SyncCenterPage"),
  "SyncCenterPage",
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

const getParentRoute = () => rootRoute;

const indexRoute = createRoute({
  getParentRoute,
  path: "/",
  component: DashboardPage,
});

const tenantsRoute = createRoute({
  getParentRoute,
  path: "/admin/tenants",
  component: TenantsPage,
});

const platformControlRoute = createRoute({
  getParentRoute,
  path: "/admin",
  component: PlatformControlPage,
});

const platformAccessRoute = createRoute({
  getParentRoute,
  path: "/admin/access",
  component: lazyRouteComponent(() => import("@/features/platformAccess/PlatformAccessPage")),
});

const platformAccountsRoute = createRoute({
  getParentRoute,
  path: "/admin/accounts",
  component: lazyRouteComponent(
    () => import("@/features/platformAccounts/PlatformAccountsPage"),
    "PlatformAccountsPage",
  ),
});

const syncCenterRoute = createRoute({
  getParentRoute,
  path: "/admin/sync",
  component: SyncCenterPage,
});

const branchesRoute = createRoute({
  getParentRoute,
  path: "/branches",
  component: BranchesPage,
});

const registersRoute = createRoute({
  getParentRoute,
  path: "/registers",
  component: RegistersPage,
});

const settingsRoute = createRoute({
  getParentRoute,
  path: "/settings",
  component: SettingsPage,
});

const usersRoute = createRoute({
  getParentRoute,
  path: "/users",
  component: UsersPage,
});

const rolesRoute = createRoute({
  getParentRoute,
  path: "/roles",
  component: RolesPage,
});

const catalogRoute = createRoute({
  getParentRoute,
  path: "/catalog",
  component: CatalogPage,
});

const batchesRoute = createRoute({
  getParentRoute,
  path: "/batches",
  component: BatchesPage,
});

const suppliersRoute = createRoute({
  getParentRoute,
  path: "/suppliers",
  component: SuppliersPage,
});

const incomingRoute = createRoute({
  getParentRoute,
  path: "/incoming",
  component: IncomingPage,
});

const incomingDetailRoute = createRoute({
  getParentRoute,
  path: "/incoming/$id",
  component: IncomingDetailPage,
});

const posRoute = createRoute({
  getParentRoute,
  path: "/pos",
  component: POSPage,
});

const salesRoute = createRoute({
  getParentRoute,
  path: "/sales",
  component: SalesPage,
});

const billingRoute = createRoute({
  getParentRoute,
  path: "/billing",
  component: BillingPage,
});

const auditRoute = createRoute({
  getParentRoute,
  path: "/audit",
  component: AuditPage,
});

const onboardingRoute = createRoute({
  getParentRoute,
  path: "/onboarding",
  component: OnboardingPage,
});

const notificationsRoute = createRoute({
  getParentRoute,
  path: "/notifications",
  component: NotificationsPage,
});

const securityRoute = createRoute({
  getParentRoute,
  path: "/security",
  component: SecurityPage,
});

const reportsRoute = createRoute({
  getParentRoute,
  path: "/reports",
  component: ReportsPage,
});

function parseLoginSearch(raw: Record<string, unknown>): { from?: string } {
  return typeof raw.from === "string" ? { from: raw.from } : {};
}

const loginRoute = createRoute({
  getParentRoute,
  path: "/login",
  component: LoginPage,
  validateSearch: parseLoginSearch,
});

function parseActivationSearch(raw: Record<string, unknown>): { token?: string } {
  return typeof raw.token === "string" ? { token: raw.token } : {};
}

const platformActivationRoute = createRoute({
  getParentRoute,
  path: "/activate-platform",
  component: PlatformActivationPage,
  validateSearch: parseActivationSearch,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  platformControlRoute,
  platformAccessRoute,
  platformAccountsRoute,
  syncCenterRoute,
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
  platformActivationRoute,
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
