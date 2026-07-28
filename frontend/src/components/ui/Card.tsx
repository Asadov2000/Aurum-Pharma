import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("rounded-lg border border-border bg-surface", className)} {...rest} />;
}

export function CardHeader({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("border-b border-border px-5 py-4", className)} {...rest} />;
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLHeadingElement>): JSX.Element {
  return <h2 className={cn("text-base font-semibold text-foreground", className)} {...rest} />;
}

export function CardContent({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("px-5 py-4", className)} {...rest} />;
}

export function CardFooter({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("border-t border-border px-5 py-3", className)} {...rest} />;
}
