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
            "grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1 px-4",
            touch ? "py-4 text-base" : "py-3 text-sm",
          )}
        >
          <div className="min-w-0">
            <p className="break-words font-medium text-foreground">{line.name}</p>
            {line.quantity_description && (
              <p className="text-xs text-foreground-muted">{line.quantity_description}</p>
            )}
          </div>
          <p className="col-start-1 row-start-2 font-mono text-xs tabular-nums text-foreground-muted">
            {line.qty}
            {line.unit_label ? ` ${line.unit_label}` : ""}
          </p>
          <p className="col-start-2 row-start-1 text-right font-mono font-semibold tabular-nums">
            {line.total_price} {currency}
          </p>
        </li>
      ))}
    </ul>
  );
}
