import { cn } from "@/lib/utils";

interface Option<Method extends string> {
  value: Method;
  label: string;
  disabled?: boolean;
}

export function PosPaymentMethodButtons<Method extends string>({
  label = "Способ оплаты",
  options,
  value,
  onChange,
  disabled = false,
  className,
}: {
  className?: string;
  label?: string;
  options: readonly Option<Method>[];
  value: Method | null;
  onChange: (method: Method) => void;
  disabled?: boolean;
}): JSX.Element {
  return (
    <div role="group" aria-label={label} className={cn("grid grid-cols-2 gap-2", className)}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          disabled={disabled || option.disabled}
          onClick={() => onChange(option.value)}
          className={cn(
            "pos-tile min-h-12 min-w-0 rounded-lg border px-2 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
            value === option.value
              ? "border-primary bg-primary/10 text-primary"
              : "border-border bg-surface text-foreground hover:bg-primary/5",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
