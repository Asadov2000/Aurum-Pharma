import { HomeIcon, type NavItem } from "./Sidebar";

/** Builds the sidebar items for the current user. Kept out of AppLayout so it
 *  can be unit-tested (and so AppLayout stays a component-only module). */
export function buildNav(
  isSupport: boolean,
  hasTenant: boolean,
  canSeeDashboard: boolean,
): NavItem[] {
  // «Главная» is the owner dashboard (gated by reports.view on the backend).
  // Hide it from users who'd only get a 403 — e.g. sellers.
  const items: NavItem[] = canSeeDashboard
    ? [{ to: "/", label: "Главная", icon: <HomeIcon /> }]
    : [];
  if (isSupport) {
    items.push({ to: "/admin/tenants", label: "Тенанты" });
  }
  if (hasTenant) {
    items.push({ to: "/onboarding", label: "Старт" });
    items.push({ to: "/branches", label: "Точки" });
    items.push({ to: "/registers", label: "Кассы" });
    items.push({ to: "/users", label: "Пользователи" });
    items.push({ to: "/roles", label: "Роли" });
    items.push({ to: "/catalog", label: "Каталог" });
    items.push({ to: "/suppliers", label: "Поставщики" });
    items.push({ to: "/incoming", label: "Приходы" });
    items.push({ to: "/batches", label: "Партии" });
    items.push({ to: "/pos", label: "Касса" });
    items.push({ to: "/sales", label: "Чеки" });
    items.push({ to: "/billing", label: "Биллинг" });
    items.push({ to: "/reports", label: "Отчёты" });
    items.push({ to: "/audit", label: "Аудит" });
    items.push({ to: "/notifications", label: "Уведомления" });
    items.push({ to: "/settings", label: "Настройки" });
  }
  return items;
}
