import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-10 w-full rounded-md border bg-white px-3 text-sm text-slate-900",
        "placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:ring-offset-1",
        "disabled:cursor-not-allowed disabled:bg-slate-50",
        invalid ? "border-red-500" : "border-slate-300",
        className,
      )}
      {...rest}
    />
  );
});
