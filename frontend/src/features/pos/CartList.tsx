import { type ReactNode } from "react";

import { Button, TableEmpty } from "@/components/ui";
import { expiryStatusFromDays } from "@/features/inventory/labels";
import { type ExpiryStatus } from "@/features/inventory/types";
import { cn } from "@/lib/utils";

import { QtyStepper } from "./QtyStepper";
import { type SaleItem } from "./types";

// Inline text colour per expiry bucket, reusing the shared ExpiryStatus/porogi:
// red/expired → danger, orange/yellow → warning, normal → muted.
const expiryTextClass: Record<ExpiryStatus, string> = {
  expired: "text-danger",
  red: "text-danger",
  orange: "text-warning-foreground",
  yellow: "text-warning-foreground",
  normal: "text-foreground-muted",
};

/** Batch number + expiry date + a coloured days-to-expiry hint for a cart line.
 *  Also disambiguates two lines of the same product (FEFO split). */
function BatchExpiry({
  item,
  fallback,
}: {
  item: SaleItem;
  fallback?: string;
}): JSX.Element | null {
  const status = expiryStatusFromDays(item.days_to_expiry);
  const date = item.expires_at ? new Date(item.expires_at).toLocaleDateString("ru-RU") : null;
  const label = item.batch_number ?? fallback ?? null;
  if (!label && !date && item.days_to_expiry == null) return null;
  return (
    <div className="truncate text-xs text-foreground-muted">
      {label && <span className="font-mono">{label}</span>}
      {label && date && " · "}
      {date && <span>до {date}</span>}
      {status && item.days_to_expiry != null && (
        <span className={cn("ml-1", expiryTextClass[status])}>
          {item.days_to_expiry >= 0
            ? `(через ${item.days_to_expiry} дн.)`
            : `(просрочена ${-item.days_to_expiry} дн.)`}
        </span>
      )}
    </div>
  );
}

interface CartGroup {
  catalogId: string;
  name: string;
  items: SaleItem[];
}

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
      <TableEmpty title="Чек пуст">Отсканируйте штрихкод или найдите товар в поиске.</TableEmpty>
    );
  }

  const windowed = items.length > 30;

  // Group all lines of the same catalog item under one product. FEFO can split
  // one product across batches into several lines; grouping is purely
  // presentational — each underlying sale item still renders as its own row
  // (and keeps data-testid="cart-item").
  const groups: CartGroup[] = [];
  const indexByCatalog = new Map<string, number>();
  for (const it of items) {
    const name = nameById[it.catalog_id] ?? it.catalog_id.slice(0, 8);
    const at = indexByCatalog.get(it.catalog_id);
    if (at === undefined) {
      indexByCatalog.set(it.catalog_id, groups.length);
      groups.push({ catalogId: it.catalog_id, name, items: [it] });
    } else {
      const grp = groups[at];
      if (grp) grp.items.push(it);
    }
  }

  // The qty stepper / line total / delete cluster — identical for a standalone
  // row and a grouped batch sub-row.
  const lineControls = (it: SaleItem, name: string): ReactNode => (
    <>
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
            touch ? "h-14 w-14 text-xl" : "h-10 w-10 text-lg",
          )}
        >
          ✕
        </Button>
      )}
    </>
  );

  return (
    <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
      {groups.map((g) => {
        const first = g.items[0];
        if (!first) return null;

        // Single line — name + controls on one compact hero row.
        if (g.items.length === 1) {
          return (
            <li
              key={g.catalogId}
              data-testid="cart-item"
              className={cn(
                "flex flex-col gap-3 px-4 sm:flex-row sm:items-center",
                touch ? "py-4" : "py-3",
                windowed && "pos-cv",
              )}
            >
              <div className="min-w-0 flex-1">
                <p className={cn("truncate font-medium text-foreground", touch && "text-lg")}>
                  {g.name}
                </p>
                <p className="text-xs text-foreground-muted">
                  {Number(first.unit_price).toFixed(2)} {currency} / шт
                </p>
                <BatchExpiry item={first} />
              </div>
              <div className="flex w-full items-center justify-end gap-1 sm:w-auto">
                {lineControls(first, g.name)}
              </div>
            </li>
          );
        }

        // FEFO-split product: one product header + a sub-row per batch line.
        const groupQty = g.items.reduce((s, it) => s + Number(it.qty), 0);
        const groupTotal = g.items.reduce((s, it) => s + Number(it.total_price), 0);
        return (
          <li key={g.catalogId} className={cn("overflow-hidden bg-surface", windowed && "pos-cv")}>
            <div className="flex items-center justify-between gap-3 border-b border-border bg-foreground/[0.02] px-4 py-2">
              <p className={cn("truncate font-medium text-foreground", touch && "text-lg")}>
                {g.name}
              </p>
              <p className="shrink-0 text-xs text-foreground-muted">
                {groupQty} шт ·{" "}
                <span className="font-mono tabular-nums text-foreground-secondary">
                  {groupTotal.toFixed(2)} {currency}
                </span>
              </p>
            </div>
            <div className="divide-y divide-border">
              {g.items.map((it, idx) => (
                <div
                  key={it.id}
                  data-testid="cart-item"
                  className={cn(
                    "flex flex-col gap-3 px-4 sm:flex-row sm:items-center",
                    touch ? "py-3" : "py-2.5",
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <BatchExpiry item={it} fallback={`Партия ${idx + 1}`} />
                    <p className="text-xs text-foreground-muted">
                      {Number(it.unit_price).toFixed(2)} {currency} / шт
                    </p>
                  </div>
                  <div className="flex w-full items-center justify-end gap-1 sm:w-auto">
                    {lineControls(it, g.name)}
                  </div>
                </div>
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
