import {
  type HTMLAttributes,
  type ReactNode,
  type TdHTMLAttributes,
  type ThHTMLAttributes,
} from "react";

import { cn } from "@/lib/utils";

export function Table({ className, ...rest }: HTMLAttributes<HTMLTableElement>): JSX.Element {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface shadow-sm">
      <table className={cn("w-full text-sm", className)} {...rest} />
    </div>
  );
}

export function THead({ children }: { children: ReactNode }): JSX.Element {
  return (
    <thead className="border-b border-border bg-foreground/[0.03] text-left">{children}</thead>
  );
}

export function TBody({ children }: { children: ReactNode }): JSX.Element {
  return <tbody className="divide-y divide-border">{children}</tbody>;
}

export function TR({ className, ...rest }: HTMLAttributes<HTMLTableRowElement>): JSX.Element {
  return <tr className={cn("transition-colors hover:bg-foreground/[0.03]", className)} {...rest} />;
}

export function TH({
  className,
  ...rest
}: ThHTMLAttributes<HTMLTableHeaderCellElement>): JSX.Element {
  return (
    <th
      className={cn(
        "px-4 py-3 text-xs font-medium uppercase tracking-wide text-foreground-muted",
        className,
      )}
      {...rest}
    />
  );
}

export function TD({ className, ...rest }: TdHTMLAttributes<HTMLTableDataCellElement>): JSX.Element {
  return <td className={cn("px-4 py-3 text-foreground", className)} {...rest} />;
}

/**
 * Friendly empty state. Pass a string child for the message; optionally add a
 * `title`, an `icon`, and an `action` (e.g. a "+ Добавить" button).
 */
export function TableEmpty({
  children,
  title,
  icon,
  action,
}: {
  children: ReactNode;
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
      {icon && <div className="text-3xl text-foreground-muted">{icon}</div>}
      {title && <p className="text-base font-medium text-foreground">{title}</p>}
      <p className="text-sm text-foreground-muted">{children}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
