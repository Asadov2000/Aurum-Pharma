import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Tone = "neutral" | "success" | "warning" | "danger" | "info";

// subtle background + readable foreground + a faint tonal inset ring for crisp
// edges; all three flip with the theme.
const toneClasses: Record<Tone, string> = {
  neutral: "bg-foreground/[0.08] text-foreground-secondary ring-foreground/15",
  success: "bg-success-subtle text-success-foreground ring-success/30",
  warning: "bg-warning-subtle text-warning-foreground ring-warning/30",
  danger: "bg-danger-subtle text-danger-foreground ring-danger/30",
  info: "bg-info-subtle text-info-foreground ring-info/30",
};

export function Badge({
  tone = "neutral",
  className,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }): JSX.Element {
  return (
    <span
      className={cn(
        "inline-flex min-h-5 items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        toneClasses[tone],
        className,
      )}
      {...rest}
    />
  );
}
