import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  meta,
  actions,
  compact = false,
  showTitleOnDesktop = false,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
  showTitleOnDesktop?: boolean;
  className?: string;
}): JSX.Element {
  const hasDesktopSupportingContent =
    description !== undefined || meta !== undefined || actions !== undefined;
  const hasDesktopText = description !== undefined || meta !== undefined;

  return (
    <header
      className={cn(
        "flex min-w-0 flex-wrap items-start justify-between gap-x-6 gap-y-3",
        compact ? "py-0.5" : "py-1",
        !hasDesktopSupportingContent && !showTitleOnDesktop && "lg:hidden",
        className,
      )}
    >
      <div
        className={cn(
          "min-w-0",
          !hasDesktopText && !showTitleOnDesktop && "lg:hidden",
          showTitleOnDesktop && "lg:block",
        )}
      >
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1
            className={cn(
              "min-w-0 break-words font-display font-semibold leading-tight text-foreground",
              !showTitleOnDesktop && "lg:hidden",
              compact ? "text-xl" : "text-2xl",
            )}
          >
            {title}
          </h1>
          {meta && <div className="text-sm text-foreground-muted">{meta}</div>}
        </div>
        {description && (
          <p className="mt-1 max-w-3xl text-sm leading-5 text-foreground-muted">{description}</p>
        )}
      </div>
      {actions && (
        <div className="flex w-full max-w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0 sm:justify-end">
          {actions}
        </div>
      )}
    </header>
  );
}
