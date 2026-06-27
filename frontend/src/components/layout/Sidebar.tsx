import { type ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";

import { cn } from "@/lib/utils";

import { NavIcon } from "./icons";

export interface NavItem {
  to: string;
  label: string;
  icon?: ReactNode;
}

interface SidebarProps {
  items: NavItem[];
  mode?: "desktop" | "drawer";
  onNavigate?: () => void;
  closeButton?: ReactNode;
}

/** Visual grouping for the flat list buildNav returns. Order here defines the
 *  sidebar order; gating is unchanged — only items buildNav actually returns
 *  get rendered, and empty sections are skipped. */
const SECTIONS: { caption?: string; routes: string[] }[] = [
  { routes: ["/"] }, // «Главная» — standalone, no caption
  { caption: "Запуск", routes: ["/onboarding"] },
  { caption: "Продажи", routes: ["/pos", "/sales"] },
  { caption: "Склад", routes: ["/catalog", "/batches", "/incoming", "/suppliers"] },
  { caption: "Аналитика", routes: ["/reports", "/audit"] },
  { caption: "Управление", routes: ["/users", "/roles", "/branches", "/registers"] },
  { caption: "Система", routes: ["/billing", "/notifications", "/settings"] },
  { caption: "Администрирование", routes: ["/admin/tenants"] },
];

export function Sidebar({
  items,
  mode = "desktop",
  onNavigate,
  closeButton,
}: SidebarProps): JSX.Element {
  const location = useRouterState({ select: (s) => s.location });
  const byRoute = new Map(items.map((i) => [i.to, i]));
  const claimed = new Set(SECTIONS.flatMap((s) => s.routes));

  const renderLink = (item: NavItem): JSX.Element => {
    const active =
      location.pathname === item.to ||
      (item.to !== "/" && location.pathname.startsWith(`${item.to}/`));
    return (
      <Link
        key={item.to}
        to={item.to}
        title={item.label}
        aria-current={active ? "page" : undefined}
        onClick={onNavigate}
        className={cn(
          "group flex min-h-9 min-w-0 items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors duration-fast",
          active
            ? "bg-primary text-primary-foreground shadow-sm"
            : "text-foreground-secondary hover:bg-foreground/5 hover:text-foreground",
        )}
      >
        <NavIcon to={item.to} />
        <span className="truncate">{item.label}</span>
      </Link>
    );
  };

  // Any item not assigned to a section (e.g. a future route) still shows up,
  // appended after the known groups, so nothing silently vanishes.
  const leftovers = items.filter((i) => !claimed.has(i.to));

  return (
    <nav
      aria-label="Основная навигация"
      className={cn(
        "flex flex-col border-r border-border bg-surface px-3 py-4",
        mode === "desktop" ? "sticky top-0 h-screen" : "h-full shadow-xl",
      )}
    >
      <div className="flex shrink-0 items-center justify-between gap-3 px-2 pb-4">
        <span className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M12 5v14M5 12h14" />
            </svg>
          </span>
          <span className="truncate font-display text-lg font-semibold text-foreground">
            Aurum Pharma
          </span>
        </span>
        {closeButton}
      </div>

      <div className="flex flex-1 flex-col gap-4 overflow-y-auto pr-1">
        {SECTIONS.map((section, idx) => {
          const present = section.routes
            .map((to) => byRoute.get(to))
            .filter((i): i is NavItem => i !== undefined);
          if (present.length === 0) return null;
          return (
            <div key={section.caption ?? `top-${idx}`} className="flex flex-col gap-0.5">
              {section.caption && (
                <div className="px-3 pb-1 pt-1 text-[11px] font-semibold uppercase text-foreground-muted">
                  {section.caption}
                </div>
              )}
              {present.map(renderLink)}
            </div>
          );
        })}

        {leftovers.length > 0 && (
          <div className="flex flex-col gap-0.5">{leftovers.map(renderLink)}</div>
        )}
      </div>
    </nav>
  );
}

/** Small inline home glyph — kept for buildNav's compatibility (it tags the
 *  «Главная» item with an icon); the sidebar itself now resolves icons by
 *  route via <NavIcon />. */
export function HomeIcon(): JSX.Element {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V21h14V9.5" />
    </svg>
  );
}
