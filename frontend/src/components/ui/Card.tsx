import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div
      className={cn(
        // Soft shadow + hairline border on a raised surface — gentle elevation.
        "rounded-xl border border-border bg-surface shadow-sm",
        className,
      )}
      {...rest}
    />
  );
}

export function CardHeader({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("border-b border-border px-6 py-4", className)} {...rest} />;
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLHeadingElement>): JSX.Element {
  return <h2 className={cn("text-lg font-semibold text-foreground", className)} {...rest} />;
}

export function CardContent({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("px-6 py-4", className)} {...rest} />;
}

export function CardFooter({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("border-t border-border px-6 py-4", className)} {...rest} />;
}
