import { forwardRef, type InputHTMLAttributes, useId } from "react";

import { cn } from "@/lib/utils";

interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
}

export const Switch = forwardRef<HTMLInputElement, SwitchProps>(function Switch(
  { label, className, id, ...rest },
  ref,
): JSX.Element {
  const reactId = useId();
  const inputId = id ?? reactId;
  return (
    <label
      htmlFor={inputId}
      className={cn(
        "inline-flex min-h-9 cursor-pointer items-center gap-2 text-sm text-foreground-secondary",
        className,
      )}
    >
      <input id={inputId} ref={ref} type="checkbox" className="peer sr-only" {...rest} />
      <span className="relative inline-block h-6 w-10 shrink-0 rounded-full bg-input transition-colors duration-fast peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background peer-disabled:opacity-50">
        <span className="absolute left-0.5 top-0.5 inline-block h-5 w-5 rounded-full bg-surface shadow-sm transition-transform duration-fast peer-checked:translate-x-4" />
      </span>
      {label}
    </label>
  );
});
