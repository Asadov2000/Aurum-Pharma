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
  primary: "bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50",
  secondary:
    "bg-surface text-foreground border border-input hover:bg-foreground/5 disabled:opacity-50",
  ghost: "bg-transparent text-foreground-secondary hover:bg-foreground/5 disabled:opacity-50",
  danger: "bg-danger text-white hover:bg-danger/90 disabled:opacity-50",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-6 text-base",
  // xl — large touch target for the upcoming POS redesign.
  xl: "h-14 px-8 text-lg",
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
    typeof children === "string" || typeof children === "number"
      ? String(children)
      : undefined;
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
        "relative inline-flex items-center justify-center gap-2 rounded-md font-semibold transition-colors duration-fast",
        "disabled:cursor-not-allowed",
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
