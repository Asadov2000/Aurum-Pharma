import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Shared container for a screen's filter row — one card style across all
 *  list screens (matches the original SalesPage look). */
export function FilterBar({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div
      className={cn(
        "flex flex-wrap items-end gap-3 rounded-md border border-border bg-surface p-3",
        className,
      )}
    >
      {children}
    </div>
  );
}
