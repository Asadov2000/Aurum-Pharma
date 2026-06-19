import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-10 w-full rounded-md border bg-surface px-3 text-sm text-foreground",
        "placeholder:text-foreground-muted transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-60",
        invalid ? "border-danger" : "border-input hover:border-foreground/25 focus:border-ring",
        className,
      )}
      {...rest}
    />
  );
});
