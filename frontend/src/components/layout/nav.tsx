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
  isSupportScoped = false,
  platformCapabilities: readonly string[] = [],
): NavItem[] {
  const context: RouteAccessContext = {
    isDeveloper,
    isAdministrator: isSupport && !isDeveloper,
    isSupportScoped,
    isTenantOwner,
    hasTenant,
    permissions,
    platformCapabilities,
  };
  const candidates: Array<NavItem & { to: AppRoutePath }> = [
    { to: "/", label: "Главная", icon: <HomeIcon /> },
    { to: "/admin", label: "Центр управления" },
    { to: "/onboarding", label: "Старт" },
    { to: "/branches", label: "Точки" },
    { to: "/registers", label: "Кассы" },
    { to: "/users", label: "Пользователи", pageTitle: "Сотрудники" },
    { to: "/roles", label: "Роли" },
    { to: "/catalog", label: "Каталог" },
    { to: "/suppliers", label: "Поставщики" },
    { to: "/incoming", label: "Приходы" },
    { to: "/batches", label: "Партии" },
    { to: "/pos", label: "Касса" },
    { to: "/sales", label: "Чеки" },
    { to: "/billing", label: "Тариф и оплата" },
    { to: "/reports", label: "Отчёты" },
    { to: "/audit", label: "Аудит" },
    { to: "/notifications", label: "Уведомления" },
    { to: "/security", label: "Безопасность" },
    { to: "/settings", label: "Настройки" },
  ];

  return candidates.filter((item) => canAccessPath(item.to, context));
}
