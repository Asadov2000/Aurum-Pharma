import { type ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import { BrandMark } from "./BrandMark";
import { NavIcon } from "./icons";

export interface NavItem {
  to: string;
  label: string;
  icon?: ReactNode;
  pageTitle?: string;
}

interface SidebarProps {
  items: NavItem[];
  mode?: "desktop" | "drawer";
  onNavigate?: () => void;
  onOpenDrawer?: (trigger: HTMLButtonElement) => void;
  drawerOpen?: boolean;
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
  {
    caption: "Система",
    routes: ["/billing", "/notifications", "/security", "/settings"],
  },
  { caption: "Администрирование", routes: ["/admin/tenants"] },
];

export function Sidebar({
  items,
  mode = "desktop",
  onNavigate,
  onOpenDrawer,
  drawerOpen = false,
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
        aria-current={active ? "page" : undefined}
        onClick={onNavigate}
        title={mode === "desktop" ? item.label : undefined}
        className={cn(
          "group flex min-w-0 items-center rounded-md text-sm font-medium transition-colors duration-fast",
          mode === "desktop"
            ? "mx-auto h-[var(--nav-target-size)] w-[var(--nav-target-size)] justify-center p-0"
            : "min-h-9 gap-2.5 px-3 py-2",
          active
            ? "bg-primary text-primary-foreground shadow-sm"
            : "text-foreground-secondary hover:bg-foreground/5 hover:text-foreground",
        )}
      >
        <NavIcon to={item.to} />
        <span className={cn("truncate", mode === "desktop" && "sr-only")}>{item.label}</span>
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
        "flex flex-col border-r border-border bg-surface",
        mode === "desktop"
          ? "sticky top-[var(--app-header-height)] h-[calc(100dvh-var(--app-header-height))] w-[var(--app-nav-rail-width)] px-2 py-2.5"
          : "h-full px-3 py-3.5 shadow-xl",
      )}
    >
      {mode === "drawer" && (
        <div className="flex shrink-0 items-center justify-between gap-3 px-2 pb-3.5">
          <BrandMark showName />
          {closeButton}
        </div>
      )}

      <div
        className={cn(
          "flex flex-1 flex-col overflow-y-auto",
          mode === "desktop" ? "gap-2" : "gap-3 pr-1",
        )}
      >
        {SECTIONS.map((section, idx) => {
          const present = section.routes
            .map((to) => byRoute.get(to))
            .filter((i): i is NavItem => i !== undefined);
          if (present.length === 0) return null;
          return (
            <div
              key={section.caption ?? `top-${idx}`}
              className={cn(
                "flex flex-col gap-0.5",
                mode === "desktop" && "border-t border-border pt-2 first:border-t-0 first:pt-0",
              )}
            >
              {mode === "drawer" && section.caption && (
                <div className="px-3 pb-1 pt-1 text-[11px] font-semibold text-foreground-muted">
                  {section.caption}
                </div>
              )}
              {present.map(renderLink)}
            </div>
          );
        })}

        {leftovers.length > 0 && (
          <div
            className={cn(
              "flex flex-col gap-0.5",
              mode === "desktop" && "border-t border-border pt-2",
            )}
          >
            {leftovers.map(renderLink)}
          </div>
        )}
      </div>

      {mode === "desktop" && onOpenDrawer && (
        <div className="mt-2 shrink-0 border-t border-border pt-2">
          <Button
            variant="ghost"
            size="sm"
            className="mx-auto h-[var(--nav-target-size)] w-[var(--nav-target-size)] px-0"
            aria-label="Показать названия разделов"
            aria-expanded={drawerOpen}
            title="Показать названия разделов"
            onClick={(event) => onOpenDrawer(event.currentTarget)}
          >
            <ExpandNavigationIcon />
          </Button>
        </div>
      )}
    </nav>
  );
}

function ExpandNavigationIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4" />
      <path d="m14 8 4 4-4 4" />
      <path d="M18 12H9" />
    </svg>
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
