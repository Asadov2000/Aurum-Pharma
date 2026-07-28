import { forwardRef, type ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg" | "xl";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  isLoading?: boolean;
}

const variantClasses: Record<Variant, string> = {
  primary:
    "border border-transparent bg-primary text-primary-foreground shadow-sm hover:bg-primary/90 active:bg-primary/80 disabled:opacity-50",
  secondary:
    "border border-input bg-surface text-foreground shadow-sm hover:border-foreground/25 hover:bg-foreground/[0.025] active:bg-foreground/5 disabled:opacity-50",
  ghost:
    "border border-transparent bg-transparent text-foreground-secondary hover:bg-foreground/5 hover:text-foreground active:bg-foreground/10 disabled:opacity-50",
  danger:
    "border border-transparent bg-danger text-danger-contrast shadow-sm hover:bg-danger/90 active:bg-danger/80 disabled:opacity-50",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-9 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-11 px-5 text-base",
  xl: "h-[52px] px-6 text-base",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    size = "md",
    isLoading,
    disabled,
    className,
    children,
    type,
    "aria-label": ariaLabel,
    ...rest
  },
  ref,
) {
  const textLabel =
    typeof children === "string" || typeof children === "number" ? String(children) : undefined;
  const loadingLabel = ariaLabel ?? textLabel;
  const mirrorLabelInCss = isLoading && textLabel !== undefined;

  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      aria-label={isLoading ? loadingLabel : ariaLabel}
      // Focus ring is provided globally by :focus-visible (see index.css).
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center gap-2 rounded-md font-semibold transition-colors duration-fast",
        "disabled:cursor-not-allowed disabled:shadow-none",
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      {...rest}
    >
      {isLoading && (
        <span
          aria-hidden="true"
          className="absolute h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      <span
        aria-hidden={isLoading || undefined}
        data-label={mirrorLabelInCss ? textLabel : undefined}
        className={cn(
          "inline-flex items-center gap-2",
          isLoading && "invisible",
          mirrorLabelInCss && "before:content-[attr(data-label)]",
        )}
      >
        {mirrorLabelInCss ? null : children}
      </span>
    </button>
  );
});
