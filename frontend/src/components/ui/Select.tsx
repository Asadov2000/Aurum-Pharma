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
        "h-10 w-full rounded-md border bg-surface px-3 text-sm text-foreground shadow-sm transition-colors duration-fast",
        "disabled:cursor-not-allowed disabled:opacity-60",
        invalid
          ? "border-danger"
          : "border-input hover:border-foreground/30 focus:border-ring focus:bg-surface-raised",
        className,
      )}
      aria-invalid={invalid || undefined}
      {...rest}
    >
      {children}
    </select>
  );
});
