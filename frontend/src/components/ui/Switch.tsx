import { type InputHTMLAttributes, useId } from "react";

import { cn } from "@/lib/utils";

interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
}

export function Switch({ label, className, id, ...rest }: SwitchProps): JSX.Element {
  const reactId = useId();
  const inputId = id ?? reactId;
  return (
    <label htmlFor={inputId} className={cn("inline-flex cursor-pointer items-center gap-2 text-sm text-slate-700", className)}>
      <input id={inputId} type="checkbox" className="peer sr-only" {...rest} />
      <span className="relative inline-block h-6 w-10 rounded-full bg-slate-300 transition-colors peer-checked:bg-slate-900 peer-disabled:opacity-50">
        <span className="absolute left-0.5 top-0.5 inline-block h-5 w-5 rounded-full bg-white shadow transition-transform peer-checked:translate-x-4" />
      </span>
      {label}
    </label>
  );
}
