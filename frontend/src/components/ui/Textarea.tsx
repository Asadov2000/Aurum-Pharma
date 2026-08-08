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
        "min-h-[var(--textarea-min-height)] w-full resize-y rounded-md border bg-surface px-[var(--field-padding-x)] py-[var(--field-padding-y)] text-sm leading-5 text-foreground shadow-sm",
        "placeholder:text-foreground-muted transition-colors duration-fast",
        "disabled:cursor-not-allowed disabled:opacity-60",
        invalid
          ? "border-danger"
          : "border-input hover:border-foreground/30 focus:border-ring focus:bg-surface-raised",
        className,
      )}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
});
