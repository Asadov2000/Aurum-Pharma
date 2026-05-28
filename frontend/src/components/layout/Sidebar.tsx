import { type ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";

import { cn } from "@/lib/utils";

export interface NavItem {
  to: string;
  label: string;
  icon?: ReactNode;
}

export function Sidebar({ items }: { items: NavItem[] }): JSX.Element {
  const location = useRouterState({ select: (s) => s.location });
  return (
    <nav className="flex h-full flex-col gap-1 border-r border-border bg-surface px-3 py-4">
      <div className="px-3 pb-4 text-lg font-semibold text-foreground">Aurum Pharma</div>
      {items.map((item) => {
        const active = location.pathname === item.to;
        return (
          <Link
            key={item.to}
            to={item.to}
            className={cn(
              "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors duration-fast",
              active
                ? "bg-primary text-primary-foreground"
                : "text-foreground-secondary hover:bg-foreground/5",
            )}
          >
            {item.icon}
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

/** Small inline home glyph — keeps the sidebar icon-free elsewhere without
 *  pulling in an icon dependency. */
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
