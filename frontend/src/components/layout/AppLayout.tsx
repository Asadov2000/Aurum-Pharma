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
  const perms = user?.permissions ?? [];
  const items = buildNav(isSupport, hasTenant, perms);

  // Clean identity: a recognizable name + a quiet caption. We have no role name
  // in the payload, so the caption is the support role (dev/admin) when set, or
  // the email for tenant users (what they recognize) — never the English "Account".
  const name = user?.full_name?.trim() || user?.email || "—";
  const caption = user?.is_developer
    ? "Разработчик"
    : user?.is_administrator
      ? "Администратор"
      : user?.full_name?.trim()
        ? (user.email ?? null)
        : null;
  const initial = name.charAt(0).toUpperCase();

  return (
    <div className="grid min-h-screen grid-cols-[240px_1fr] bg-background">
      <Sidebar items={items} />
      <div className="flex min-w-0 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <span
              aria-hidden="true"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-primary/10 font-display text-sm font-semibold text-primary"
            >
              {initial}
            </span>
            <div className="min-w-0 leading-tight">
              <div className="truncate text-sm font-semibold text-foreground">{name}</div>
              {caption && (
                <div className="truncate text-xs text-foreground-muted">{caption}</div>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3">
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
