import { type HTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function FormError({
  children,
  className,
  ...rest
}: {
  children: ReactNode;
} & HTMLAttributes<HTMLParagraphElement>): JSX.Element | null {
  if (!children) return null;
  return (
    <p className={cn("mt-1.5 text-sm text-danger", className)} role="alert" {...rest}>
      {children}
    </p>
  );
}
