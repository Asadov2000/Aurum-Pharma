import { Button, TableEmpty } from "@/components/ui";
import { cn } from "@/lib/utils";

import { QtyStepper } from "./QtyStepper";
import { type SaleItem } from "./types";

export function CartList({
  items,
  nameById,
  currency,
  editable,
  onQtyChange,
  onDelete,
  onQtyTap,
  touch,
  busy,
}: {
  items: SaleItem[];
  nameById: Record<string, string>;
  currency: string;
  editable: boolean;
  onQtyChange: (itemId: string, qty: number) => void;
  onDelete: (itemId: string) => void;
  onQtyTap?: (itemId: string) => void;
  touch?: boolean;
  busy?: boolean;
}): JSX.Element {
  if (items.length === 0) {
    return (
      <TableEmpty icon="🧾" title="Чек пуст">
        Отсканируйте штрихкод или найдите товар в поиске.
      </TableEmpty>
    );
  }

  return (
    <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
      {items.map((it) => {
        const name = nameById[it.catalog_id] ?? it.catalog_id.slice(0, 8);
        return (
          <li
            key={it.id}
            className={cn("flex items-center gap-3 px-4", touch ? "py-4" : "py-3")}
          >
            <div className="min-w-0 flex-1">
              <p
                className={cn(
                  "truncate font-medium text-foreground",
                  touch && "text-lg",
                )}
              >
                {name}
              </p>
              <p className="text-xs text-foreground-muted">
                {Number(it.unit_price).toFixed(2)} {currency} / шт
              </p>
            </div>

            {editable ? (
              <QtyStepper
                value={Number(it.qty)}
                onChange={(q) => onQtyChange(it.id, q)}
                onValueTap={onQtyTap ? () => onQtyTap(it.id) : undefined}
                disabled={busy}
                size={touch ? "lg" : "md"}
              />
            ) : (
              <span className="font-mono tabular-nums text-foreground">×{Number(it.qty)}</span>
            )}

            <div
              className={cn(
                "w-24 text-right font-mono font-semibold tabular-nums text-foreground",
                touch && "text-lg",
              )}
            >
              {Number(it.total_price).toFixed(2)}
            </div>

            {editable && (
              <Button
                variant="ghost"
                onClick={() => onDelete(it.id)}
                disabled={busy}
                aria-label={`Удалить ${name}`}
                className={cn(
                  "rounded-md p-0 text-foreground-muted hover:text-danger",
                  touch ? "h-14 w-14 text-xl" : "h-9 w-9",
                )}
              >
                ✕
              </Button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
