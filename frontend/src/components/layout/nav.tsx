import { HomeIcon, type NavItem } from "./Sidebar";

/** Builds the sidebar items for the current user. Kept out of AppLayout so it
 *  can be unit-tested (and so AppLayout stays a component-only module). */
export function buildNav(
  isSupport: boolean,
  hasTenant: boolean,
  isTenantOwner: boolean,
  permissions: readonly string[],
): NavItem[] {
  const can = (code: string): boolean => isSupport || permissions.includes(code);
  const canAny = (codes: readonly string[]): boolean => codes.some((code) => can(code));
  const hasPermission = (code: string): boolean => permissions.includes(code);
  const hasAnyPermission = (codes: readonly string[]): boolean =>
    codes.some((code) => hasPermission(code));

  // «Главная» is the owner dashboard (gated by reports.view on the backend).
  // Hide it from users who'd only get a 403 — e.g. sellers.
  const items: NavItem[] = can("reports.view")
    ? [{ to: "/", label: "Главная", icon: <HomeIcon /> }]
    : [];
  if (isSupport) {
    items.push({ to: "/admin/tenants", label: "Тенанты" });
  }
  if (hasTenant) {
    items.push({ to: "/onboarding", label: "Старт" });
    items.push({ to: "/branches", label: "Точки" });
    items.push({ to: "/registers", label: "Кассы" });
    if (hasPermission("users.view")) {
      items.push({ to: "/users", label: "Пользователи" });
    }
    if (
      isTenantOwner &&
      hasAnyPermission(["roles.create", "roles.update", "roles.assign"])
    ) {
      items.push({ to: "/roles", label: "Роли" });
    }
    if (can("catalog.view")) items.push({ to: "/catalog", label: "Каталог" });
    if (can("suppliers.view")) items.push({ to: "/suppliers", label: "Поставщики" });
    if (can("incoming.view")) items.push({ to: "/incoming", label: "Приходы" });
    if (can("reports.view")) items.push({ to: "/batches", label: "Партии" });
    if (canAny(["pos.shift_open", "pos.sell", "pos.shift_close"])) {
      items.push({ to: "/pos", label: "Касса" });
    }
    if (canAny(["sales.view.own", "sales.view.tenant"])) {
      items.push({ to: "/sales", label: "Чеки" });
    }
    if (can("reports.view")) {
      items.push({ to: "/billing", label: "Биллинг" });
      items.push({ to: "/reports", label: "Отчёты" });
    }
    if (canAny(["audit.view.own", "audit.view.tenant", "audit.view.global"])) {
      items.push({ to: "/audit", label: "Аудит" });
    }
    items.push({ to: "/notifications", label: "Уведомления" });
    if (can("settings.update")) items.push({ to: "/settings", label: "Настройки" });
  }
  return items;
}
