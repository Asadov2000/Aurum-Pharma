import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div
      className={cn(
        "rounded-lg border border-slate-200 bg-white shadow-sm",
        className,
      )}
      {...rest}
    />
  );
}

export function CardHeader({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("border-b border-slate-200 px-6 py-4", className)} {...rest} />;
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLHeadingElement>): JSX.Element {
  return (
    <h2 className={cn("text-lg font-semibold text-slate-900", className)} {...rest} />
  );
}

export function CardContent({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("px-6 py-4", className)} {...rest} />;
}

export function CardFooter({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn("border-t border-slate-200 px-6 py-4", className)} {...rest} />;
}
