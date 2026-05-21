import { type HTMLAttributes, type ReactNode, type TdHTMLAttributes, type ThHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Table({ className, ...rest }: HTMLAttributes<HTMLTableElement>): JSX.Element {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className={cn("w-full text-sm", className)} {...rest} />
    </div>
  );
}

export function THead({ children }: { children: ReactNode }): JSX.Element {
  return <thead className="border-b border-slate-200 bg-slate-50 text-left">{children}</thead>;
}

export function TBody({ children }: { children: ReactNode }): JSX.Element {
  return <tbody className="divide-y divide-slate-200">{children}</tbody>;
}

export function TR({ className, ...rest }: HTMLAttributes<HTMLTableRowElement>): JSX.Element {
  return <tr className={cn("hover:bg-slate-50", className)} {...rest} />;
}

export function TH({ className, ...rest }: ThHTMLAttributes<HTMLTableHeaderCellElement>): JSX.Element {
  return <th className={cn("px-4 py-3 text-xs font-medium uppercase tracking-wide text-slate-600", className)} {...rest} />;
}

export function TD({ className, ...rest }: TdHTMLAttributes<HTMLTableDataCellElement>): JSX.Element {
  return <td className={cn("px-4 py-3 text-slate-800", className)} {...rest} />;
}

export function TableEmpty({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-500">
      {children}
    </div>
  );
}
