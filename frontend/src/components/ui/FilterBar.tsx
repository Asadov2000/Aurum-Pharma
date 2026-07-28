import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

/** One dense, responsive toolbar for list filters and their actions. */
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
        "flex min-w-0 flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-3",
        className,
      )}
    >
      {children}
    </div>
  );
}
