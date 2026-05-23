import { type ReactNode } from "react";

import { Button } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";

import { Sidebar, type NavItem } from "./Sidebar";

function buildNav(isSupport: boolean, hasTenant: boolean): NavItem[] {
  const items: NavItem[] = [];
  if (isSupport) {
    items.push({ to: "/admin/tenants", label: "Тенанты" });
  }
  if (hasTenant) {
    items.push({ to: "/branches", label: "Точки" });
    items.push({ to: "/registers", label: "Кассы" });
    items.push({ to: "/users", label: "Пользователи" });
    items.push({ to: "/roles", label: "Роли" });
    items.push({ to: "/catalog", label: "Каталог" });
    items.push({ to: "/suppliers", label: "Поставщики" });
    items.push({ to: "/incoming", label: "Приходы" });
    items.push({ to: "/batches", label: "Партии" });
    items.push({ to: "/pos", label: "Касса" });
    items.push({ to: "/billing", label: "Биллинг" });
    items.push({ to: "/settings", label: "Настройки" });
  }
  items.push({ to: "/", label: "Профиль" });
  return items;
}

export function AppLayout({ children }: { children: ReactNode }): JSX.Element {
  const { user, logout } = useAuth();
  const isSupport = Boolean(user?.is_developer || user?.is_administrator);
  const hasTenant = Boolean(user?.home_tenant_id);
  const items = buildNav(isSupport, hasTenant);

  return (
    <div className="grid min-h-screen grid-cols-[220px_1fr] bg-slate-50">
      <Sidebar items={items} />
      <div className="flex flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
          <div className="text-sm text-slate-600">
            <span className="font-medium text-slate-900">{user?.full_name}</span>
            <span className="ml-2 text-slate-500">({user?.email})</span>
          </div>
          <Button variant="secondary" size="sm" onClick={() => void logout()}>
            Выйти
          </Button>
        </header>
        <main className="flex-1 px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
