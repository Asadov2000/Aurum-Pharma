import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const Checkbox = forwardRef<
  HTMLInputElement,
  Omit<InputHTMLAttributes<HTMLInputElement>, "type">
>(function Checkbox({ className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      type="checkbox"
      className={cn(
        "h-4 w-4 shrink-0 rounded border-input accent-primary disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...rest}
    />
  );
});
