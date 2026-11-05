import { cn } from "@/lib/utils";

import { type ReceiptLine } from "./types";

/** Historical receipt facts only: no draft identities, catalogue lookup or controls. */
export function CompletedReceiptLines({
  lines,
  currency,
  touch,
}: {
  lines: ReceiptLine[];
  currency: string;
  touch: boolean;
}): JSX.Element {
  return (
    <ul
      aria-label="Товары завершённого чека"
      className="min-h-0 flex-1 divide-y divide-border overflow-y-auto bg-surface"
    >
      {lines.map((line) => (
        <li
          key={line.position}
          className={cn(
            "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-4 sm:grid-cols-[minmax(0,1fr)_8.5rem_5.5rem_2.5rem]",
            touch ? "py-4 text-base" : "py-3 text-sm",
          )}
        >
          <div className="min-w-0">
            <p className="break-words font-medium text-foreground">{line.name}</p>
            {line.quantity_description && (
              <p className="text-xs text-foreground-muted">{line.quantity_description}</p>
            )}
          </div>
          <p className="font-mono tabular-nums sm:text-center">
            {line.qty}
            {line.unit_label ? ` ${line.unit_label}` : ""}
          </p>
          <p className="col-span-2 text-right font-mono tabular-nums sm:col-span-1">
            {line.total_price} {currency}
          </p>
          <span aria-hidden="true" className="hidden sm:block" />
        </li>
      ))}
    </ul>
  );
}
