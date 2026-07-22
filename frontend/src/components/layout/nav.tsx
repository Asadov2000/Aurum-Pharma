import { HomeIcon, type NavItem } from "./Sidebar";
import { canAccessPath, type AppRoutePath, type RouteAccessContext } from "./routeAccess";

/** Builds the sidebar items for the current user. Kept out of AppLayout so it
 *  can be unit-tested (and so AppLayout stays a component-only module). */
export function buildNav(
  isSupport: boolean,
  hasTenant: boolean,
  isTenantOwner: boolean,
  permissions: readonly string[],
  isDeveloper = false,
): NavItem[] {
  const context: RouteAccessContext = {
    isDeveloper,
    isAdministrator: isSupport && !isDeveloper,
    isTenantOwner,
    hasTenant,
    permissions,
  };
  const candidates: Array<NavItem & { to: AppRoutePath }> = [
    { to: "/", label: "Главная", icon: <HomeIcon /> },
    { to: "/admin/tenants", label: "Тенанты" },
    { to: "/onboarding", label: "Старт" },
    { to: "/branches", label: "Точки" },
    { to: "/registers", label: "Кассы" },
    { to: "/users", label: "Пользователи" },
    { to: "/roles", label: "Роли" },
    { to: "/catalog", label: "Каталог" },
    { to: "/suppliers", label: "Поставщики" },
    { to: "/incoming", label: "Приходы" },
    { to: "/batches", label: "Партии" },
    { to: "/pos", label: "Касса" },
    { to: "/sales", label: "Чеки" },
    { to: "/billing", label: "Биллинг" },
    { to: "/reports", label: "Отчёты" },
    { to: "/audit", label: "Аудит" },
    { to: "/notifications", label: "Уведомления" },
    { to: "/security", label: "Безопасность" },
    { to: "/settings", label: "Настройки" },
  ];

  return candidates.filter((item) => canAccessPath(item.to, context));
}
