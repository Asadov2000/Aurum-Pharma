import { type MeResponse } from "@/features/auth/types";
import { activeTenantId } from "@/features/auth/tenantContext";

const TENANTS_VIEW_CAPABILITY = "platform.tenants.view";
const GLOBAL_AUDIT_VIEW_CAPABILITY = "platform.audit.global.view";
const PLATFORM_ACCESS_VIEW_CAPABILITY = "platform.access.view";
const PLATFORM_ACCOUNTS_VIEW_CAPABILITY = "platform.accounts.view";
const PLATFORM_SYNC_VIEW_CAPABILITY = "platform.sync.view";

export const BRANCH_VIEW_PERMISSIONS = ["branches.view"] as const;
export const REGISTER_VIEW_PERMISSIONS = ["registers.view"] as const;
export const ROLE_MANAGEMENT_PERMISSIONS = [
  "roles.create",
  "roles.update",
  "roles.assign",
] as const;
export const POS_PERMISSIONS = ["pos.shift_open", "pos.shift_close", "pos.sell"] as const;
export const SALES_VIEW_PERMISSIONS = ["sales.view.own", "sales.view.tenant"] as const;
export const AUDIT_VIEW_PERMISSIONS = [
  "audit.view.own",
  "audit.view.tenant",
  "audit.view.global",
] as const;

export type AppRoutePath =
  | "/"
  | "/admin"
  | "/admin/access"
  | "/admin/accounts"
  | "/admin/sync"
  | "/admin/tenants"
  | "/onboarding"
  | "/branches"
  | "/registers"
  | "/users"
  | "/roles"
  | "/catalog"
  | "/batches"
  | "/suppliers"
  | "/incoming"
  | "/pos"
  | "/sales"
  | "/billing"
  | "/reports"
  | "/audit"
  | "/notifications"
  | "/security"
  | "/settings";

export interface RouteAccessContext {
  isDeveloper: boolean;
  isAdministrator: boolean;
  isSupportScoped: boolean;
  isTenantOwner: boolean;
  hasTenant: boolean;
  permissions: readonly string[];
  platformCapabilities: readonly string[];
}

export function getRouteAccessContext(user: MeResponse | null | undefined): RouteAccessContext {
  const identity: Partial<MeResponse> = user ?? {};
  return {
    isDeveloper: !!identity.is_developer,
    isAdministrator: !!identity.is_administrator,
    isSupportScoped: !!identity.support_access,
    isTenantOwner: !!identity.is_tenant_owner,
    hasTenant: Boolean(activeTenantId(user)),
    permissions: identity.permissions ?? [],
    platformCapabilities: identity.platform_capabilities ?? [],
  };
}

function hasPermission(context: RouteAccessContext, code: string): boolean {
  // The backend has a temporary developer bypass for ordinary tenant routes;
  // administrator access still comes from the scoped permission snapshot.
  return (context.isDeveloper && !context.isSupportScoped) || context.permissions.includes(code);
}

function hasAnyPermission(context: RouteAccessContext, codes: readonly string[]): boolean {
  return codes.some((code) => hasPermission(context, code));
}

function hasPlatformCapability(context: RouteAccessContext, code: string): boolean {
  return context.platformCapabilities.includes(code);
}

function isPath(pathname: string, route: AppRoutePath): boolean {
  return pathname === route || pathname.startsWith(`${route}/`);
}

/**
 * Client-side route visibility is an early UX gate only. Every API endpoint
 * still performs the authoritative server-side authorization check.
 */
export function canAccessPath(pathname: string, context: RouteAccessContext): boolean {
  if (pathname === "/login" || pathname === "/activate-platform") return true;
  if (context.isSupportScoped && pathname.startsWith("/admin")) return false;
  const canGovernPlatformAccess =
    context.isDeveloper && hasPlatformCapability(context, PLATFORM_ACCESS_VIEW_CAPABILITY);
  const canViewPlatformAccounts = hasPlatformCapability(context, PLATFORM_ACCOUNTS_VIEW_CAPABILITY);
  const canViewPlatformSync = hasPlatformCapability(context, PLATFORM_SYNC_VIEW_CAPABILITY);
  if (pathname === "/admin") {
    return (
      hasPlatformCapability(context, TENANTS_VIEW_CAPABILITY) ||
      hasPlatformCapability(context, GLOBAL_AUDIT_VIEW_CAPABILITY) ||
      canGovernPlatformAccess ||
      canViewPlatformAccounts ||
      canViewPlatformSync
    );
  }
  if (pathname === "/admin/access") {
    return canGovernPlatformAccess;
  }
  if (pathname === "/admin/accounts") {
    return canViewPlatformAccounts;
  }
  if (pathname === "/admin/sync") {
    return canViewPlatformSync;
  }
  if (isPath(pathname, "/admin/tenants")) {
    return hasPlatformCapability(context, TENANTS_VIEW_CAPABILITY);
  }
  if (pathname.startsWith("/admin/")) {
    return false;
  }
  if (pathname === "/") {
    return context.hasTenant
      ? hasPermission(context, "reports.view")
      : context.isDeveloper || context.isAdministrator;
  }
  // Notifications are tied to the authenticated account, not to a tenant
  // permission, and therefore remain available to support users too.
  if (isPath(pathname, "/notifications")) return true;
  // Session inventory is account-scoped and deliberately independent of a
  // tenant role, so a cashier can protect their own account as well.
  if (isPath(pathname, "/security")) return true;
  // Global audit is capability-scoped and does not require a selected tenant.
  // Scoped tenant audit still follows explicit permissions.
  if (isPath(pathname, "/audit")) {
    return (
      (!context.isSupportScoped && hasPlatformCapability(context, GLOBAL_AUDIT_VIEW_CAPABILITY)) ||
      (context.hasTenant && hasAnyPermission(context, AUDIT_VIEW_PERMISSIONS))
    );
  }

  if (!context.hasTenant) return false;

  if (isPath(pathname, "/onboarding") || isPath(pathname, "/settings")) {
    return hasPermission(context, "settings.update");
  }
  if (isPath(pathname, "/branches")) {
    return hasAnyPermission(context, BRANCH_VIEW_PERMISSIONS);
  }
  if (isPath(pathname, "/registers")) {
    return hasAnyPermission(context, REGISTER_VIEW_PERMISSIONS);
  }
  if (isPath(pathname, "/users")) {
    return hasPermission(context, "users.view");
  }
  if (isPath(pathname, "/roles")) {
    return (
      (context.isSupportScoped && hasAnyPermission(context, ROLE_MANAGEMENT_PERMISSIONS)) ||
      (context.isTenantOwner && hasAnyPermission(context, ROLE_MANAGEMENT_PERMISSIONS))
    );
  }
  if (isPath(pathname, "/catalog")) {
    return hasPermission(context, "catalog.view");
  }
  if (isPath(pathname, "/batches")) {
    return hasPermission(context, "batches.view");
  }
  if (isPath(pathname, "/suppliers")) {
    return hasPermission(context, "suppliers.view");
  }
  if (isPath(pathname, "/incoming")) {
    return hasPermission(context, "incoming.view");
  }
  if (isPath(pathname, "/pos")) {
    return hasAnyPermission(context, POS_PERMISSIONS);
  }
  if (isPath(pathname, "/sales")) {
    return hasAnyPermission(context, SALES_VIEW_PERMISSIONS);
  }
  if (isPath(pathname, "/billing") || isPath(pathname, "/reports")) {
    return hasPermission(context, "reports.view");
  }
  // A scoped support identity is fail-closed: a newly added route must opt in
  // before it can become visible inside a tenant context.
  return !context.isSupportScoped;
}

const FALLBACK_PATHS: readonly AppRoutePath[] = [
  "/",
  "/pos",
  "/catalog",
  "/sales",
  "/incoming",
  "/notifications",
  "/admin",
];

export function firstAccessiblePath(context: RouteAccessContext): AppRoutePath | null {
  return FALLBACK_PATHS.find((path) => canAccessPath(path, context)) ?? null;
}
