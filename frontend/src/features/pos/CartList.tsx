import { useRef, type ReactNode } from "react";

import { Button, TableEmpty } from "@/components/ui";
import { RecordActionMenu, type RecordAction } from "@/features/foundation/RecordActionMenu";
import { expiryStatusFromDays } from "@/features/inventory/labels";
import { type ExpiryStatus } from "@/features/inventory/types";
import { type QuickActionMenuPreferences } from "@/features/settings/quickActions";
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

interface CartQuickActions {
  config: QuickActionMenuPreferences;
  scope: string;
}

function CartItemActionMenu({
  item,
  name,
  knownName,
  batchFallback,
  quickActions,
  editable,
  busy,
  actionsDisabled,
  onQtyChange,
  onDelete,
}: {
  item: SaleItem;
  name: string;
  knownName?: string;
  batchFallback?: string;
  quickActions: CartQuickActions;
  editable: boolean;
  busy?: boolean;
  actionsDisabled?: boolean;
  onQtyChange: (itemId: string, qty: number) => void;
  onDelete: (itemId: string) => void;
}): JSX.Element {
  const current = useRef({ item, editable, busy, actionsDisabled, onQtyChange, onDelete });
  current.current = { item, editable, busy, actionsDisabled, onQtyChange, onDelete };
  const changeQty = (direction: 1 | -1) => {
    const latest = current.current;
    if (!latest.editable || latest.busy || latest.actionsDisabled) return;
    const qty = Number(latest.item.qty);
    if (direction === -1 && qty <= 1) return;
    latest.onQtyChange(latest.item.id, direction === 1 ? qty + 1 : Math.max(1, qty - 1));
  };
  const actions: RecordAction[] = [];
  if (knownName?.trim())
    actions.push({ id: "copy_name", label: "Скопировать название", copyValue: knownName });
  if (item.batch_number?.trim())
    actions.push({
      id: "copy_number",
      label: "Скопировать номер партии",
      copyValue: item.batch_number,
    });
  if (editable && !busy && !actionsDisabled) {
    actions.push({
      id: "increase_qty",
      label: "Увеличить количество",
      onSelect: () => changeQty(1),
    });
    if (Number(item.qty) > 1)
      actions.push({
        id: "decrease_qty",
        label: "Уменьшить количество",
        onSelect: () => changeQty(-1),
      });
    actions.push({
      id: "remove",
      label: "Удалить из чека",
      tone: "danger",
      onSelect: () => {
        const latest = current.current;
        if (latest.editable && !latest.busy && !latest.actionsDisabled)
          latest.onDelete(latest.item.id);
      },
    });
  }
  const batch = item.batch_number?.trim()
    ? `Партия ${item.batch_number}`
    : (batchFallback ?? "Партия без номера");
  const detail = item.expires_at
    ? `${batch} · до ${new Date(item.expires_at).toLocaleDateString("ru-RU")}`
    : batch;
  return (
    <RecordActionMenu
      section="pos_cart"
      recordKey={JSON.stringify([
        item.sale_id,
        item.id,
        item.catalog_id,
        item.batch_id,
        item.qty,
        item.unit_price,
        item.total_price,
        editable,
        busy,
        actionsDisabled,
        knownName,
        item.batch_number,
        item.expires_at,
        item.days_to_expiry,
      ])}
      title={name}
      detail={detail}
      label={`Действия ${name}, ${detail}`}
      items={actions}
      config={quickActions.config}
      scope={quickActions.scope}
      disabled={busy || actionsDisabled}
    />
  );
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
  quickActions,
  actionsDisabled,
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
  quickActions?: CartQuickActions;
  actionsDisabled?: boolean;
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

  // Shared qty stepper / line total / delete cluster for each sale item.
  const lineControls = (
    it: SaleItem,
    name: string,
    batchFallback?: string,
    stacked = false,
  ): ReactNode => (
    <div
      className={cn(
        "grid w-full min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2",
        !stacked && "sm:flex sm:w-auto sm:justify-end sm:gap-1",
      )}
      data-testid="cart-line-controls"
    >
      <div className={cn("col-span-2 flex min-w-0 justify-start", !stacked && "sm:contents")}>
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

      {(editable || quickActions) && (
        <div
          className={cn(
            quickActions ? "flex items-center justify-end gap-1" : "contents",
            quickActions &&
              touch &&
              "[&>button[aria-haspopup=menu]]:h-14 [&>button[aria-haspopup=menu]]:w-14",
          )}
        >
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
          {quickActions && (
            <CartItemActionMenu
              item={it}
              name={name}
              knownName={nameById[it.catalog_id]}
              batchFallback={batchFallback}
              quickActions={quickActions}
              editable={editable}
              busy={busy}
              actionsDisabled={actionsDisabled}
              onQtyChange={onQtyChange}
              onDelete={onDelete}
            />
          )}
        </div>
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

        // Touch controls need the full receipt width once a row has both delete
        // and a menu. Keep its name above the existing mobile controls grid.
        if (g.items.length === 1) {
          const stacked = Boolean(touch && quickActions);
          return (
            <li
              key={g.catalogId}
              data-testid="cart-item"
              data-context-actions={quickActions ? true : undefined}
              tabIndex={quickActions ? 0 : undefined}
              className={cn(
                "flex flex-col gap-3 px-4",
                !stacked && "sm:flex-row sm:items-center",
                touch ? "py-5" : "py-4",
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
              {lineControls(first, g.name, undefined, stacked)}
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
                  data-context-actions={quickActions ? true : undefined}
                  tabIndex={quickActions ? 0 : undefined}
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
                  {lineControls(it, g.name, `Партия ${idx + 1}`)}
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
