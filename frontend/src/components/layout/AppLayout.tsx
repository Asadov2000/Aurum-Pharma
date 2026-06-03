import { type ReactNode } from "react";

import { Button } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/features/auth/hooks";

import { Sidebar } from "./Sidebar";
import { buildNav } from "./nav";

export function AppLayout({ children }: { children: ReactNode }): JSX.Element {
  const { user, logout } = useAuth();
  const isSupport = Boolean(user?.is_developer || user?.is_administrator);
  const hasTenant = Boolean(user?.home_tenant_id);
  const canSeeDashboard = isSupport || (user?.permissions ?? []).includes("reports.view");
  const items = buildNav(isSupport, hasTenant, canSeeDashboard);

  return (
    <div className="grid min-h-screen grid-cols-[220px_1fr] bg-background">
      <Sidebar items={items} />
      <div className="flex flex-col">
        <header className="flex items-center justify-between border-b border-border bg-surface px-6 py-3">
          <div className="text-sm text-foreground-secondary">
            <span className="font-medium text-foreground">{user?.full_name}</span>
            <span className="ml-2 text-foreground-muted">({user?.email})</span>
          </div>
          <div className="flex items-center gap-3">
            <ThemeToggle />
            <Button variant="secondary" size="sm" onClick={() => void logout()}>
              Выйти
            </Button>
          </div>
        </header>
        <main className="flex-1 px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
