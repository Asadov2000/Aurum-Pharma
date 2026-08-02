import { type ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import { BrandMark } from "./BrandMark";
import { NavIcon } from "./icons";
import { SIDEBAR_SECTIONS } from "./sidebarPreferences";

export interface NavItem {
  to: string;
  label: string;
  icon?: ReactNode;
  pageTitle?: string;
}

interface SidebarProps {
  items: NavItem[];
  mode?: "desktop" | "drawer";
  expanded?: boolean;
  favoriteRoutes?: readonly string[];
  onNavigate?: () => void;
  onToggleExpanded?: () => void;
  onOpenSettings?: () => void;
  closeButton?: ReactNode;
}

export function Sidebar({
  items,
  mode = "desktop",
  expanded = true,
  favoriteRoutes = [],
  onNavigate,
  onToggleExpanded,
  onOpenSettings,
  closeButton,
}: SidebarProps): JSX.Element {
  const location = useRouterState({ select: (state) => state.location });
  const showLabels = mode === "drawer" || expanded;
  const claimed = new Set(SIDEBAR_SECTIONS.flatMap((section) => section.routes));
  const favorites = new Set(favoriteRoutes);
  const favoriteItems = items.filter((item) => favorites.has(item.to));

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
        title={!showLabels ? item.label : undefined}
        className={cn(
          "group relative flex min-w-0 items-center rounded-md text-sm font-medium transition-colors duration-fast",
          showLabels
            ? "min-h-[var(--nav-target-size)] w-full gap-3 px-3 py-2"
            : "mx-auto h-[var(--nav-target-size)] w-[var(--nav-target-size)] justify-center p-0",
          active
            ? "bg-primary text-primary-foreground shadow-sm"
            : "text-foreground-secondary hover:bg-foreground/5 hover:text-foreground",
        )}
      >
        <NavIcon to={item.to} />
        <span className={cn("truncate", !showLabels && "sr-only")}>{item.label}</span>
      </Link>
    );
  };

  const renderGroup = (
    key: string,
    caption: string | undefined,
    groupItems: NavItem[],
    first = false,
  ): JSX.Element | null => {
    if (groupItems.length === 0) return null;
    return (
      <div
        key={key}
        className={cn(
          "flex flex-col gap-0.5",
          !showLabels && !first && "border-t border-border pt-2",
        )}
      >
        {showLabels && caption && (
          <div className="px-3 pb-1 pt-2 text-[11px] font-semibold text-foreground-muted">
            {caption}
          </div>
        )}
        {groupItems.map(renderLink)}
      </div>
    );
  };

  let renderedGroups = 0;
  const nextGroup = (
    key: string,
    caption: string | undefined,
    groupItems: NavItem[],
  ): JSX.Element | null => {
    const rendered = renderGroup(key, caption, groupItems, renderedGroups === 0);
    if (rendered !== null) renderedGroups += 1;
    return rendered;
  };

  const leftovers = items.filter((item) => !claimed.has(item.to) && !favorites.has(item.to));

  return (
    <nav
      aria-label="Основная навигация"
      data-sidebar-mode={mode === "drawer" ? "drawer" : expanded ? "expanded" : "compact"}
      className={cn(
        "flex flex-col border-r border-border bg-surface",
        mode === "desktop"
          ? "sticky top-[var(--app-header-height)] h-[calc(100dvh-var(--app-header-height))] w-full px-2 py-2.5"
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
        className={cn("flex flex-1 flex-col overflow-y-auto", showLabels ? "gap-2 pr-1" : "gap-2")}
      >
        {nextGroup("favorites", "Избранное", favoriteItems)}
        {SIDEBAR_SECTIONS.map((section) =>
          nextGroup(
            section.id,
            section.caption,
            items.filter((item) => section.routes.includes(item.to) && !favorites.has(item.to)),
          ),
        )}
        {nextGroup("other", "Другое", leftovers)}
      </div>

      {(onOpenSettings || (mode === "desktop" && onToggleExpanded)) && (
        <div className={cn("mt-2 shrink-0 border-t border-border pt-2", showLabels && "space-y-1")}>
          {onOpenSettings && (
            <Button
              variant="ghost"
              size="sm"
              className={cn(
                showLabels
                  ? "min-h-[var(--nav-target-size)] w-full justify-start px-3 font-medium"
                  : "mx-auto h-[var(--nav-target-size)] w-[var(--nav-target-size)] px-0",
              )}
              aria-label="Настроить боковую панель"
              title={!showLabels ? "Настроить боковую панель" : undefined}
              onClick={onOpenSettings}
            >
              <NavigationSettingsIcon />
              <span className={cn(!showLabels && "sr-only")}>Настроить меню</span>
            </Button>
          )}
          {mode === "desktop" && onToggleExpanded && (
            <Button
              variant="ghost"
              size="sm"
              className={cn(
                showLabels
                  ? "min-h-[var(--nav-target-size)] w-full justify-start px-3 font-medium"
                  : "mx-auto h-[var(--nav-target-size)] w-[var(--nav-target-size)] px-0",
              )}
              aria-label={expanded ? "Свернуть боковую панель" : "Развернуть боковую панель"}
              aria-expanded={expanded}
              title={!showLabels ? "Развернуть боковую панель" : undefined}
              onClick={onToggleExpanded}
            >
              <ExpandNavigationIcon expanded={expanded} />
              <span className={cn(!showLabels && "sr-only")}>
                {expanded ? "Свернуть" : "Развернуть"}
              </span>
            </Button>
          )}
        </div>
      )}
    </nav>
  );
}

function ExpandNavigationIcon({ expanded }: { expanded: boolean }): JSX.Element {
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
      <path d={expanded ? "m16 8-4 4 4 4" : "m14 8 4 4-4 4"} />
      <path d={expanded ? "M12 12h7" : "M18 12H9"} />
    </svg>
  );
}

function NavigationSettingsIcon(): JSX.Element {
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
    >
      <path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6" />
    </svg>
  );
}

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
