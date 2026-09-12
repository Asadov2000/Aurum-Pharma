import { cn } from "@/lib/utils";
import { type ContactChannel } from "./identityApi";

export function LoginMethodSwitch({
  value,
  onChange,
  disabled = false,
  label = "Способ входа",
}: {
  value: ContactChannel;
  onChange: (value: ContactChannel) => void;
  disabled?: boolean;
  label?: string;
}): JSX.Element {
  return (
    <div role="group" aria-label={label} className="mb-6 flex gap-1 rounded-lg bg-foreground/5 p-1">
      {(
        [
          ["email", "Почта"],
          ["phone", "Телефон"],
        ] as const
      ).map(([method, text]) => (
        <button
          key={method}
          type="button"
          aria-pressed={value === method}
          disabled={disabled}
          onClick={() => onChange(method)}
          className={cn(
            "min-h-11 flex-1 rounded-md px-3 text-sm transition-colors disabled:opacity-50 motion-reduce:transition-none",
            value === method
              ? "bg-surface font-semibold text-primary shadow-sm"
              : "text-foreground-secondary hover:text-foreground",
          )}
        >
          {text}
        </button>
      ))}
    </div>
  );
}
