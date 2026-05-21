import { Link, useRouterState } from "@tanstack/react-router";

import { cn } from "@/lib/utils";

export interface NavItem {
  to: string;
  label: string;
}

export function Sidebar({ items }: { items: NavItem[] }): JSX.Element {
  const location = useRouterState({ select: (s) => s.location });
  return (
    <nav className="flex h-full flex-col gap-1 border-r border-slate-200 bg-white px-3 py-4">
      <div className="px-3 pb-4 text-lg font-semibold text-slate-900">Aurum Pharma</div>
      {items.map((item) => {
        const active = location.pathname === item.to;
        return (
          <Link
            key={item.to}
            to={item.to}
            className={cn(
              "rounded-md px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-slate-900 text-white"
                : "text-slate-700 hover:bg-slate-100",
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
