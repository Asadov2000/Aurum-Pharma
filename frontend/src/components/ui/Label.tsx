import { type LabelHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Label({ className, ...rest }: LabelHTMLAttributes<HTMLLabelElement>): JSX.Element {
  return (
    <label
      className={cn("mb-1 block text-sm font-medium text-foreground-secondary", className)}
      {...rest}
    />
  );
}
