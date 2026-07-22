import { type MeResponse } from "@/features/auth/types";

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
  isTenantOwner: boolean;
  hasTenant: boolean;
  permissions: readonly string[];
}

export function getRouteAccessContext(user: MeResponse | null | undefined): RouteAccessContext {
  return {
    isDeveloper: user?.is_developer === true,
    isAdministrator: user?.is_administrator === true,
    isTenantOwner: user?.is_tenant_owner === true,
    hasTenant: Boolean(user?.home_tenant_id),
    permissions: user?.permissions ?? [],
  };
}

function hasPermission(context: RouteAccessContext, code: string): boolean {
  // The backend has a temporary developer bypass for ordinary tenant routes;
  // administrator access still comes from the scoped permission snapshot.
  return context.isDeveloper || context.permissions.includes(code);
}

function hasAnyPermission(context: RouteAccessContext, codes: readonly string[]): boolean {
  return codes.some((code) => hasPermission(context, code));
}

function isPath(pathname: string, route: AppRoutePath): boolean {
  return pathname === route || pathname.startsWith(`${route}/`);
}

/**
 * Client-side route visibility is an early UX gate only. Every API endpoint
 * still performs the authoritative server-side authorization check.
 */
export function canAccessPath(pathname: string, context: RouteAccessContext): boolean {
  if (pathname === "/login") return true;
  if (isPath(pathname, "/admin/tenants")) {
    return context.isDeveloper || context.isAdministrator;
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
  // Global audit is a developer-only support surface and does not require a
  // selected tenant. Scoped tenant audit still follows explicit permissions.
  if (isPath(pathname, "/audit")) {
    return (
      context.isDeveloper ||
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
      context.isDeveloper ||
      context.isAdministrator ||
      (context.isTenantOwner && hasAnyPermission(context, ROLE_MANAGEMENT_PERMISSIONS))
    );
  }
  if (isPath(pathname, "/catalog")) {
    return hasPermission(context, "catalog.view");
  }
  if (isPath(pathname, "/batches")) {
    return hasPermission(context, "reports.view");
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
  // Unknown paths are left to the router's not-found handling. This keeps a
  // future route from being accidentally hidden until its capability is added.
  return true;
}

const FALLBACK_PATHS: readonly AppRoutePath[] = [
  "/",
  "/pos",
  "/catalog",
  "/sales",
  "/incoming",
  "/notifications",
  "/admin/tenants",
];

export function firstAccessiblePath(context: RouteAccessContext): AppRoutePath | null {
  return FALLBACK_PATHS.find((path) => canAccessPath(path, context)) ?? null;
}

export const routeLabel: Record<AppRoutePath, string> = {
  "/": "Главная",
  "/admin/tenants": "Аптеки",
  "/onboarding": "Старт",
  "/branches": "Точки",
  "/registers": "Кассы",
  "/users": "Пользователи",
  "/roles": "Роли",
  "/catalog": "Каталог",
  "/batches": "Партии",
  "/suppliers": "Поставщики",
  "/incoming": "Приходы",
  "/pos": "Касса",
  "/sales": "Чеки",
  "/billing": "Биллинг",
  "/reports": "Отчёты",
  "/audit": "Аудит",
  "/notifications": "Уведомления",
  "/security": "Безопасность",
  "/settings": "Настройки",
};
