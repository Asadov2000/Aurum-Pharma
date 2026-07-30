import { useState } from "react";

import { Button, Skeleton } from "@/components/ui";
import { useCatalogQuery } from "@/features/catalog/queries";
import { type CatalogItem } from "@/features/catalog/types";
import { cn } from "@/lib/utils";

type QuickView = "grid" | "list";

const productAccents = [
  {
    shell: "border-[#d9c9ef] bg-[#f4effb] text-[#6f3fa0]",
    stripe: "bg-[#8a5ab8]",
  },
  {
    shell: "border-[#c9d9ef] bg-[#eef4fb] text-[#315f9c]",
    stripe: "bg-[#477ab8]",
  },
  {
    shell: "border-[#f0c9c5] bg-[#fff1ef] text-[#a43f35]",
    stripe: "bg-[#d95347]",
  },
  {
    shell: "border-[#bcdfe3] bg-[#edf9fa] text-[#126b76]",
    stripe: "bg-[#2795a2]",
  },
] as const;

export function QuickProducts({
  branchId,
  onAdd,
  busy = false,
  touch = false,
}: {
  branchId?: string;
  onAdd: (catalogId: string, name: string, qty: number) => boolean | void | Promise<boolean | void>;
  busy?: boolean;
  touch?: boolean;
}): JSX.Element {
  const [view, setView] = useState<QuickView>("grid");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const catalog = useCatalogQuery({
    page: 1,
    page_size: 4,
    branch_id: branchId,
  });

  const addProduct = async (item: CatalogItem) => {
    if (pendingId !== null || busy) return;
    setPendingId(item.id);
    try {
      await onAdd(item.id, item.brand_name, 1);
    } finally {
      setPendingId(null);
    }
  };

  return (
    <section
      aria-labelledby="quick-products-title"
      className="flex min-h-[30rem] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-surface xl:h-[36rem]"
    >
      <header className="flex min-h-14 items-center justify-between gap-3 border-b border-border px-4">
        <div className="min-w-0">
          <h2
            id="quick-products-title"
            className="truncate text-base font-semibold text-foreground"
          >
            Быстрый выбор
          </h2>
          <p className="text-xs text-foreground-muted">Товары для продажи в одно нажатие</p>
        </div>
        <div className="flex shrink-0 items-center rounded-md border border-border bg-background p-0.5">
          <button
            type="button"
            aria-label="Показать товары плиткой"
            aria-pressed={view === "grid"}
            title="Плитка"
            onClick={() => setView("grid")}
            className={cn(
              "grid h-8 w-8 place-items-center rounded text-foreground-muted transition-colors duration-fast",
              view === "grid" && "bg-surface text-primary shadow-sm",
            )}
          >
            <GridIcon />
          </button>
          <button
            type="button"
            aria-label="Показать товары списком"
            aria-pressed={view === "list"}
            title="Список"
            onClick={() => setView("list")}
            className={cn(
              "grid h-8 w-8 place-items-center rounded text-foreground-muted transition-colors duration-fast",
              view === "list" && "bg-surface text-primary shadow-sm",
            )}
          >
            <ListIcon />
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
        {catalog.isLoading ? (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(12rem,1fr))] gap-2.5">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-56 rounded-md" />
            ))}
          </div>
        ) : catalog.error ? (
          <div
            role="alert"
            className="flex min-h-48 items-center justify-center px-6 text-center text-sm text-danger"
          >
            Не удалось загрузить быстрый выбор.
          </div>
        ) : catalog.data?.items.length ? (
          <div
            className={cn(
              "grid gap-2.5",
              view === "grid" ? "grid-cols-[repeat(auto-fit,minmax(12rem,1fr))]" : "grid-cols-1",
            )}
          >
            {catalog.data.items.map((item, index) => {
              const accent = productAccents[index % productAccents.length] ?? productAccents[0];
              const stock = item.stock_available == null ? null : Number(item.stock_available);
              const unavailable = stock !== null && stock <= 0;
              const adding = pendingId === item.id;

              return (
                <article
                  key={item.id}
                  className={cn(
                    "grid min-w-0 overflow-hidden rounded-md border border-border bg-surface-raised",
                    view === "grid"
                      ? "grid-rows-[minmax(8.5rem,1fr)_auto]"
                      : "grid-cols-[minmax(0,1fr)_9rem]",
                  )}
                >
                  <div className="grid min-w-0 grid-cols-[5rem_minmax(0,1fr)] gap-3 p-3">
                    <div
                      aria-hidden="true"
                      className={cn(
                        "relative my-auto flex h-24 w-20 shrink-0 items-center justify-center overflow-hidden rounded border shadow-sm",
                        accent.shell,
                      )}
                    >
                      <span className={cn("absolute inset-y-0 right-0 w-2", accent.stripe)} />
                      <span className="max-w-[3.75rem] break-words text-center text-[11px] font-bold leading-tight">
                        {shortProductName(item.brand_name)}
                      </span>
                    </div>

                    <div className="flex min-w-0 flex-col">
                      <h3 className="line-clamp-2 text-sm font-semibold leading-5 text-foreground">
                        {item.brand_name}
                      </h3>
                      <p className="mt-0.5 truncate text-xs text-foreground-muted">
                        {[item.dosage, item.pack_size].filter(Boolean).join(" · ") ||
                          item.form ||
                          "Лекарственный товар"}
                      </p>
                      <p
                        className={cn(
                          "mt-2 text-xs font-medium",
                          unavailable ? "text-danger" : "text-success-foreground",
                        )}
                      >
                        {stock === null
                          ? "В наличии"
                          : unavailable
                            ? "Нет в наличии"
                            : `В наличии: ${formatStock(stock)} шт.`}
                      </p>
                      <div className="mt-auto pt-2">
                        <p className="text-[10px] font-medium text-foreground-muted">
                          Базовая цена
                        </p>
                        <p className="font-mono text-xl font-bold tabular-nums text-foreground">
                          {item.base_price ? Number(item.base_price).toFixed(2) : "—"}{" "}
                          <span className="font-sans text-xs font-semibold text-foreground-secondary">
                            {item.currency}
                          </span>
                        </p>
                      </div>
                    </div>
                  </div>

                  <Button
                    type="button"
                    className={cn(
                      "m-1.5 mt-0",
                      view === "list" && "m-2 self-center",
                      touch && "min-h-12",
                    )}
                    aria-label={`Добавить ${item.brand_name}`}
                    isLoading={adding}
                    disabled={busy || unavailable}
                    onClick={() => void addProduct(item)}
                  >
                    <CartIcon />
                    Добавить
                  </Button>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="flex min-h-48 items-center justify-center px-6 text-center text-sm text-foreground-muted">
            В каталоге пока нет доступных товаров.
          </div>
        )}
      </div>
    </section>
  );
}

function shortProductName(value: string): string {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "AP";
  return words.slice(0, 2).join(" ").slice(0, 16);
}

function formatStock(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function GridIcon(): JSX.Element {
  return (
    <svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
      <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" />
    </svg>
  );
}

function ListIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <path d="M9 6h11M9 12h11M9 18h11" />
      <path d="M4 6h.01M4 12h.01M4 18h.01" />
    </svg>
  );
}

function CartIcon(): JSX.Element {
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
      <path d="M3 4h2l2.2 10.2a2 2 0 0 0 2 1.6h7.9a2 2 0 0 0 2-1.6L20.5 8H6" />
      <circle cx="10" cy="20" r="1" />
      <circle cx="18" cy="20" r="1" />
    </svg>
  );
}
