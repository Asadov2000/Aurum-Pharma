import { cn } from "@/lib/utils";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  label,
  size = "md",
  className,
}: {
  value: T;
  options: readonly SegmentOption<T>[];
  onChange: (value: T) => void;
  label: string;
  size?: "sm" | "md";
  className?: string;
}): JSX.Element {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn(
        "inline-flex max-w-full items-center rounded-md border border-border bg-background p-0.5",
        className,
      )}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            title={option.title}
            aria-pressed={selected}
            onClick={() => onChange(option.value)}
            className={cn(
              "min-w-0 rounded-md font-medium transition-colors duration-fast",
              size === "sm" ? "h-7 px-2 text-xs" : "h-8 px-3 text-sm",
              selected
                ? "bg-surface text-foreground shadow-sm"
                : "text-foreground-muted hover:text-foreground",
            )}
          >
            <span className="block truncate">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
