import { forwardRef, type SelectHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { invalid, className, children, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      className={cn(
        "h-10 w-full rounded-md border bg-white px-3 text-sm text-slate-900",
        "focus:outline-none focus:ring-2 focus:ring-slate-900 focus:ring-offset-1",
        "disabled:cursor-not-allowed disabled:bg-slate-50",
        invalid ? "border-red-500" : "border-slate-300",
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  );
});
