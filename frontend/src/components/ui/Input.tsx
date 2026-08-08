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
        "h-[var(--control-height-md)] w-full rounded-md border bg-surface px-[var(--field-padding-x)] text-sm text-foreground shadow-sm",
        "placeholder:text-foreground-muted transition-colors duration-fast",
        "disabled:cursor-not-allowed disabled:opacity-60",
        invalid
          ? "border-danger"
          : "border-input hover:border-foreground/30 focus:border-ring focus:bg-surface-raised",
        className,
      )}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
});
