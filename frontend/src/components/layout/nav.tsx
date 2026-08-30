import { HomeIcon, type NavItem } from "./Sidebar";
import { canAccessPath, type AppRoutePath, type RouteAccessContext } from "./routeAccess";

export interface AppNavItem extends NavItem {
  to: AppRoutePath;
}

export function findActiveNavItem(
  items: readonly NavItem[],
  pathname: string,
): NavItem | undefined {
  return items.reduce<NavItem | undefined>((bestMatch, item) => {
    const matches =
      pathname === item.to || (item.to !== "/" && pathname.startsWith(`${item.to}/`));
    if (!matches) return bestMatch;
    return !bestMatch || item.to.length > bestMatch.to.length ? item : bestMatch;
  }, undefined);
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
    { to: "/admin", label: "Центр управления" },
    { to: "/admin/billing", label: "Расчёты Aurum" },
    { to: "/onboarding", label: "Старт" },
    { to: "/branches", label: "Торговые точки" },
    { to: "/registers", label: "Рабочие кассы" },
    { to: "/users", label: "Сотрудники" },
    { to: "/roles", label: "Роли" },
    { to: "/catalog", label: "Каталог" },
    { to: "/suppliers", label: "Поставщики" },
    { to: "/incoming", label: "Приходы" },
    { to: "/batches", label: "Партии" },
    { to: "/pos", label: "Касса" },
    { to: "/sales", label: "Чеки" },
    { to: "/payment-reconciliation", label: "Сверка оплат" },
    { to: "/billing", label: "Тариф и оплата" },
    { to: "/reports", label: "Отчёты" },
    { to: "/audit", label: "Аудит" },
    { to: "/notifications", label: "Уведомления" },
    { to: "/security", label: "Безопасность" },
    { to: "/settings", label: "Настройки" },
  ];

  return candidates.filter((item) => canAccessPath(item.to, context));
}
