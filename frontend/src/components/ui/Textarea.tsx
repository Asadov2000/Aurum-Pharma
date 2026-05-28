import { forwardRef, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid, className, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      className={cn(
        "min-h-[80px] w-full rounded-md border bg-surface px-3 py-2 text-sm text-foreground",
        "placeholder:text-foreground-muted",
        "disabled:cursor-not-allowed disabled:opacity-60",
        invalid ? "border-danger" : "border-input",
        className,
      )}
      {...rest}
    />
  );
});
