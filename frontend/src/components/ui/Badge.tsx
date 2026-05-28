import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Tone = "neutral" | "success" | "warning" | "danger" | "info";

// subtle background + readable foreground; both flip with the theme.
const toneClasses: Record<Tone, string> = {
  neutral: "bg-foreground/8 text-foreground-secondary",
  success: "bg-success-subtle text-success-foreground",
  warning: "bg-warning-subtle text-warning-foreground",
  danger: "bg-danger-subtle text-danger-foreground",
  info: "bg-info-subtle text-info-foreground",
};

export function Badge({
  tone = "neutral",
  className,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }): JSX.Element {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        toneClasses[tone],
        className,
      )}
      {...rest}
    />
  );
}
