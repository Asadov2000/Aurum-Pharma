import { useState } from "react";

import { Button, Skeleton } from "@/components/ui";
import { type CatalogItem } from "@/features/catalog/types";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { usePosFavoritesQuery, useRemovePosFavorite } from "./queries";

type QuickView = "grid" | "list";

const productAccents = [
  {
    shell: "border-primary/20 bg-primary/5 text-primary",
    stripe: "bg-primary/60",
  },
  {
    shell: "border-info/20 bg-info-subtle text-info",
    stripe: "bg-info/60",
  },
  {
    shell: "border-warning/20 bg-warning-subtle text-warning",
    stripe: "bg-warning/60",
  },
  {
    shell: "border-success/20 bg-success-subtle text-success-foreground",
    stripe: "bg-success/60",
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
  const [favoriteError, setFavoriteError] = useState<string | null>(null);
  const favorites = usePosFavoritesQuery(branchId);
  const removeFavorite = useRemovePosFavorite();
  const products = favorites.data?.map((favorite) => favorite.catalog) ?? [];

  const addProduct = async (item: CatalogItem) => {
    if (pendingId !== null || busy) return;
    setPendingId(item.id);
    try {
      await onAdd(item.id, item.brand_name, 1);
    } finally {
      setPendingId(null);
    }
  };

  const removeProduct = async (item: CatalogItem) => {
    if (removeFavorite.isPending) return;
    setFavoriteError(null);
    try {
      await removeFavorite.mutateAsync(item.id);
    } catch (error) {
      setFavoriteError(describeApiError(error, "Не удалось убрать товар из избранного"));
    }
  };

  return (
    <section
      aria-labelledby="quick-products-title"
      className="flex min-h-[26rem] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-surface sm:min-h-[30rem] xl:h-full xl:min-h-0"
    >
      <header className="flex min-h-14 items-center justify-between gap-3 border-b border-border px-4">
        <div className="min-w-0">
          <h2
            id="quick-products-title"
            className="truncate text-base font-semibold text-foreground"
          >
            Быстрый выбор
          </h2>
          <p className="text-xs text-foreground-muted">Личное избранное</p>
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
        {favorites.isLoading ? (
          <div className="grid grid-cols-1 gap-2">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-36 rounded-md" />
            ))}
          </div>
        ) : favorites.error ? (
          <div
            role="alert"
            className="flex min-h-48 flex-col items-center justify-center gap-3 px-6 text-center"
          >
            <p className="text-sm text-danger">Не удалось загрузить избранные товары.</p>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => void favorites.refetch()}
            >
              Повторить
            </Button>
          </div>
        ) : products.length ? (
          <div className="grid grid-cols-1 gap-2">
            {products.map((item, index) => {
              const accent = productAccents[index % productAccents.length] ?? productAccents[0];
              const stock = item.stock_available == null ? null : Number(item.stock_available);
              const unavailable = !item.is_active || (stock !== null && stock <= 0);
              const adding = pendingId === item.id;

              return (
                <article
                  key={item.id}
                  className="relative flex min-w-0 flex-col overflow-hidden rounded-md border border-border bg-surface"
                >
                  <button
                    type="button"
                    aria-label={`Убрать ${item.brand_name} из избранного`}
                    title="Убрать из избранного"
                    disabled={removeFavorite.isPending}
                    onClick={() => void removeProduct(item)}
                    className="absolute right-1.5 top-1.5 z-10 grid h-9 w-9 place-items-center rounded-md text-warning hover:bg-warning-subtle disabled:opacity-50"
                  >
                    <StarIcon />
                  </button>
                  <div
                    className={cn(
                      "grid min-w-0 gap-3 p-3 pr-11",
                      view === "grid"
                        ? "grid-cols-[2.75rem_minmax(0,1fr)]"
                        : "grid-cols-[2rem_minmax(0,1fr)] gap-2 py-2.5",
                    )}
                  >
                    <div
                      aria-hidden="true"
                      className={cn(
                        "relative my-auto flex shrink-0 items-center justify-center overflow-hidden rounded border",
                        view === "grid" ? "h-[60px] w-11" : "h-11 w-8",
                        accent.shell,
                      )}
                    >
                      <span className={cn("absolute inset-y-0 right-0 w-1", accent.stripe)} />
                      <span className="max-w-full break-words px-1.5 text-center text-[8px] font-bold leading-tight">
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
                          "mt-1 text-xs font-medium",
                          unavailable ? "text-danger" : "text-success-foreground",
                        )}
                      >
                        {!item.is_active
                          ? "Снят с продажи"
                          : stock === null
                            ? "В наличии"
                            : unavailable
                              ? "Нет в наличии"
                              : `В наличии: ${formatStock(stock)} шт.`}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center justify-between gap-2 border-t border-border bg-background px-3 py-1.5">
                    <div className="min-w-0">
                      <p className="text-[10px] font-medium text-foreground-muted">Базовая цена</p>
                      <p className="font-mono text-sm font-bold tabular-nums text-foreground">
                        {item.base_price ? Number(item.base_price).toFixed(2) : "—"}{" "}
                        <span className="font-sans text-[10px] font-semibold text-foreground-secondary">
                          {item.currency}
                        </span>
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className={cn(
                        "shrink-0 text-primary hover:bg-primary/5",
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
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="flex min-h-48 flex-col items-center justify-center gap-1 px-6 text-center">
            <p className="text-sm font-medium text-foreground">Избранных товаров пока нет</p>
            <p className="max-w-sm text-sm text-foreground-muted">
              Найдите товар в строке поиска и нажмите звезду. Он появится здесь только для вашего
              аккаунта.
            </p>
          </div>
        )}
        {favoriteError ? (
          <p className="mt-2 text-center text-xs text-danger" role="alert">
            {favoriteError}
          </p>
        ) : null}
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

function StarIcon(): JSX.Element {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
      <path d="m12 2.8 2.8 5.7 6.3.9-4.6 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3-4.6-4.4 6.3-.9z" />
    </svg>
  );
}
