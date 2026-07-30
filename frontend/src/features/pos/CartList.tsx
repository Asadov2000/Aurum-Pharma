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
  embedded = false,
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
  embedded?: boolean;
}): JSX.Element {
  if (items.length === 0) {
    if (embedded) {
      return (
        <div className="flex min-h-64 flex-1 flex-col items-center justify-center gap-3 px-6 py-10 text-center">
          <span
            aria-hidden="true"
            className="grid h-12 w-12 place-items-center rounded-full bg-primary/10 text-primary"
          >
            <ReceiptIcon />
          </span>
          <div>
            <p className="font-medium text-foreground">Чек пуст</p>
            <p className="mt-1 max-w-72 text-sm text-foreground-muted">
              Отсканируйте штрихкод, найдите товар или добавьте его из быстрого выбора.
            </p>
          </div>
        </div>
      );
    }
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
    <div
      className="grid w-full min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:flex sm:w-auto sm:justify-end sm:gap-1"
      data-testid="cart-line-controls"
    >
      <div className="col-span-2 flex min-w-0 justify-start sm:contents">
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
      </div>

      <div
        className={cn(
          "min-w-0 text-left font-mono font-semibold tabular-nums text-foreground sm:w-24 sm:text-right",
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
            touch ? "h-14 w-14" : "h-10 w-10",
          )}
        >
          <TrashIcon />
        </Button>
      )}
    </div>
  );

  return (
    <ul
      className={cn(
        "divide-y divide-border bg-surface",
        embedded
          ? "min-h-0 flex-1 overflow-y-auto"
          : "overflow-hidden rounded-lg border border-border",
      )}
    >
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
              {lineControls(first, g.name)}
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
                  {lineControls(it, g.name)}
                </div>
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function TrashIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 7h16" />
      <path d="M9 7V4h6v3" />
      <path d="m6 7 1 13h10l1-13" />
      <path d="M10 11v5M14 11v5" />
    </svg>
  );
}

function ReceiptIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9 8h6M9 12h6" />
    </svg>
  );
}
