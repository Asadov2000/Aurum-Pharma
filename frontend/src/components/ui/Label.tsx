import { type LabelHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Label({ className, ...rest }: LabelHTMLAttributes<HTMLLabelElement>): JSX.Element {
  return (
    <label className={cn("block text-sm font-medium text-slate-700", className)} {...rest} />
  );
}
