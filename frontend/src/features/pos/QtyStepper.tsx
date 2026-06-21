import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

/**
 * Quantity stepper: big −/+ buttons around the current value. In touch mode
 * the buttons grow (POS part 2); the value can be tapped to open the NumPad.
 */
export function QtyStepper({
  value,
  onChange,
  onValueTap,
  disabled,
  size = "md",
}: {
  value: number;
  onChange: (next: number) => void;
  onValueTap?: () => void;
  disabled?: boolean;
  size?: "md" | "lg";
}): JSX.Element {
  const dec = () => onChange(Math.max(1, value - 1));
  const inc = () => onChange(value + 1);
  const btn = size === "lg" ? "h-14 w-14 text-2xl" : "h-10 w-10 text-lg";
  const val = size === "lg" ? "w-16 text-2xl" : "w-12 text-base";

  return (
    <div className="inline-flex items-center gap-1">
      <Button
        type="button"
        variant="secondary"
        onClick={dec}
        disabled={disabled || value <= 1}
        aria-label="Уменьшить количество"
        className={cn("rounded-md p-0", btn)}
      >
        −
      </Button>
      <button
        type="button"
        onClick={onValueTap}
        disabled={disabled || !onValueTap}
        className={cn(
          "text-center font-mono font-semibold text-foreground tabular-nums",
          val,
          onValueTap && "rounded-md hover:bg-foreground/5",
        )}
        aria-label="Количество"
      >
        {value}
      </button>
      <Button
        type="button"
        variant="secondary"
        onClick={inc}
        disabled={disabled}
        aria-label="Увеличить количество"
        className={cn("rounded-md p-0", btn)}
      >
        +
      </Button>
    </div>
  );
}
