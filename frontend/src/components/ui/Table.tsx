import {
  type HTMLAttributes,
  type ReactNode,
  type TdHTMLAttributes,
  type ThHTMLAttributes,
} from "react";

import { cn } from "@/lib/utils";

export function Table({ className, ...rest }: HTMLAttributes<HTMLTableElement>): JSX.Element {
  return (
    <div className="w-full min-w-0 max-w-full overflow-x-auto rounded-lg border border-border bg-surface [contain:paint]">
      <table className={cn("w-full min-w-max text-sm", className)} {...rest} />
    </div>
  );
}

export function THead({ children }: { children: ReactNode }): JSX.Element {
  return <thead className="border-b border-border bg-background text-left">{children}</thead>;
}

export function TBody({ children }: { children: ReactNode }): JSX.Element {
  return <tbody className="divide-y divide-border">{children}</tbody>;
}

export function TR({ className, ...rest }: HTMLAttributes<HTMLTableRowElement>): JSX.Element {
  return (
    <tr
      className={cn("transition-colors duration-fast hover:bg-foreground/[0.025]", className)}
      {...rest}
    />
  );
}

export function TH({
  className,
  ...rest
}: ThHTMLAttributes<HTMLTableHeaderCellElement>): JSX.Element {
  return (
    <th
      className={cn(
        "whitespace-nowrap px-[var(--table-cell-padding-x)] py-[var(--table-head-padding-y)] text-xs font-semibold text-foreground-muted",
        className,
      )}
      {...rest}
    />
  );
}

export function TD({
  className,
  ...rest
}: TdHTMLAttributes<HTMLTableDataCellElement>): JSX.Element {
  return (
    <td
      className={cn(
        "px-[var(--table-cell-padding-x)] py-[var(--table-cell-padding-y)] text-foreground",
        className,
      )}
      {...rest}
    />
  );
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
    <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-surface px-6 py-10 text-center">
      {icon && <div className="text-2xl text-foreground-muted">{icon}</div>}
      {title && <p className="text-base font-medium text-foreground">{title}</p>}
      <p className="text-sm text-foreground-muted">{children}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
