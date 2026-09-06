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
        "inline-flex min-h-[var(--control-height-sm)] cursor-pointer items-center gap-2 text-sm text-foreground-secondary",
        className,
      )}
    >
      <input id={inputId} ref={ref} type="checkbox" className="peer sr-only" {...rest} />
      <span
        aria-hidden="true"
        className="relative inline-block h-6 w-10 shrink-0 rounded-full bg-input transition-colors duration-200 ease-out peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background peer-disabled:opacity-50 peer-checked:[&>span]:translate-x-4 peer-checked:[&_svg]:scale-100 peer-checked:[&_svg]:opacity-100"
      >
        <span className="absolute left-0.5 top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-surface text-primary shadow-sm transition-transform duration-200 ease-out">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-3.5 w-3.5 scale-75 opacity-0 transition-[opacity,transform] duration-200 ease-out"
          >
            <path d="m6 12 4 4 8-8" />
          </svg>
        </span>
      </span>
      {label}
    </label>
  );
});
