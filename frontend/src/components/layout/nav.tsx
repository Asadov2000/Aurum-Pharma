import { HomeIcon, type NavItem } from "./Sidebar";
import { canAccessPath, type AppRoutePath, type RouteAccessContext } from "./routeAccess";

export interface AppNavItem extends NavItem {
  to: AppRoutePath;
}

export function findActiveNavItem(
  items: readonly NavItem[],
  pathname: string,
): NavItem | undefined {
  let active: NavItem | undefined;
  for (const item of items) {
    const matches = pathname === item.to || (item.to !== "/" && pathname.startsWith(item.to + "/"));
    if (matches && (!active || item.to.length > active.to.length)) active = item;
  }
  return active;
}

/** Builds the sidebar items for the current user. Kept out of AppLayout so it
 *  can be unit-tested (and so AppLayout stays a component-only module). */
export function buildNav(
  isSupport: boolean,
  hasTenant: boolean,
  isTenantOwner: boolean,
  permissions: readonly string[],
  isDeveloper = false,
  isSupportScoped = false,
  platformCapabilities: readonly string[] = [],
): AppNavItem[] {
  const context: RouteAccessContext = {
    isDeveloper,
    isAdministrator: isSupport && !isDeveloper,
    isSupportScoped,
    isTenantOwner,
    hasTenant,
    permissions,
    platformCapabilities,
  };
  const candidates: AppNavItem[] = [
    { to: "/", label: "Главная", icon: <HomeIcon /> },
    { to: "/admin", label: "Управление" },
    { to: "/admin/billing", label: "Расчёты Aurum" },
    { to: "/onboarding", label: "Старт" },
    { to: "/branches", label: "Точки" },
    { to: "/registers", label: "Кассы" },
    { to: "/users", label: "Сотрудники" },
    { to: "/roles", label: "Роли" },
    { to: "/catalog", label: "Каталог" },
    { to: "/suppliers", label: "Поставщики" },
    { to: "/incoming", label: "Приходы" },
    { to: "/batches", label: "Партии" },
    { to: "/pos", label: "Касса" },
    { to: "/sales", label: "Чеки" },
    { to: "/payment-reconciliation", label: "Сверка" },
    { to: "/billing", label: "Тариф" },
    { to: "/reports", label: "Отчёты" },
    { to: "/audit", label: "Аудит" },
    { to: "/notifications", label: "Уведомления" },
    { to: "/security", label: "Безопасность" },
    { to: "/settings", label: "Настройки" },
  ];

  return candidates.filter((item) => canAccessPath(item.to, context));
}
