import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function FormError({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): JSX.Element | null {
  if (!children) return null;
  return <p className={cn("mt-1 text-sm text-red-600", className)}>{children}</p>;
}
