import { useState } from "react";

import { Button, Skeleton } from "@/components/ui";
import { useCatalogQuery } from "@/features/catalog/queries";
import { type CatalogItem } from "@/features/catalog/types";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { useAddPosFavorite, usePosFavoritesQuery, useRemovePosFavorite } from "./queries";

type QuickView = "grid" | "list";

export function QuickProducts({
  branchId,
  onAdd,
  onSelect,
  busy = false,
  touch = false,
  selectedCatalogId,
}: {
  branchId?: string;
  onAdd: (catalogId: string, name: string, qty: number) => boolean | void | Promise<boolean | void>;
  onSelect?: (item: CatalogItem) => boolean | void | Promise<boolean | void>;
  busy?: boolean;
  touch?: boolean;
  selectedCatalogId?: string | null;
}): JSX.Element {
  const [view, setView] = useState<QuickView>("list");
  const [filter, setFilter] = useState<"all" | "favorites">("all");
  const [pagination, setPagination] = useState({ branchId, page: 1 });
  const page = pagination.branchId === branchId ? pagination.page : 1;
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [favoriteError, setFavoriteError] = useState<string | null>(null);
  const favorites = usePosFavoritesQuery(branchId);
  const catalog = useCatalogQuery(
    { branch_id: branchId, lifecycle: "active", page, page_size: 20 },
    Boolean(branchId) && filter === "all",
  );
  const addFavorite = useAddPosFavorite();
  const removeFavorite = useRemovePosFavorite();
  const favoriteIds = new Set(favorites.data?.map((favorite) => favorite.catalog_id));
  const source = filter === "all" ? catalog : favorites;
  // Previous-page data must never be selectable after a branch or page change.
  const products = !branchId
    ? []
    : filter === "all"
      ? catalog.isPlaceholderData
        ? []
        : (catalog.data?.items ?? [])
      : (favorites.data?.map((favorite) => favorite.catalog) ?? []);
  const favoritePending = addFavorite.isPending || removeFavorite.isPending;

  const addProduct = async (item: CatalogItem) => {
    if (pendingId !== null || busy) return;
    setPendingId(item.id);
    try {
      if (onSelect) await onSelect(item);
      else await onAdd(item.id, item.brand_name, 1);
    } finally {
      setPendingId(null);
    }
  };

  const toggleFavorite = async (item: CatalogItem) => {
    if (favoritePending) return;
    setFavoriteError(null);
    const removing = favoriteIds.has(item.id);
    try {
      await (removing ? removeFavorite : addFavorite).mutateAsync(item.id);
    } catch (error) {
      setFavoriteError(
        describeApiError(
          error,
          removing
            ? "Не удалось убрать товар из избранного"
            : "Не удалось добавить товар в избранное",
        ),
      );
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
        </div>
        <div className="flex shrink-0 items-center rounded-md border border-border bg-background p-0.5">
          <button
            type="button"
            aria-label="Показать товары плиткой"
            aria-pressed={view === "grid"}
            title="Плитка"
            onClick={() => setView("grid")}
            className={cn(
              "grid place-items-center rounded text-foreground-muted transition-colors duration-fast",
              touch ? "h-12 w-12" : "h-9 w-9",
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
              "grid place-items-center rounded text-foreground-muted transition-colors duration-fast",
              touch ? "h-12 w-12" : "h-9 w-9",
              view === "list" && "bg-surface text-primary shadow-sm",
            )}
          >
            <ListIcon />
          </button>
        </div>
      </header>

      <div
        className="flex gap-2 border-b border-border p-3"
        role="group"
        aria-label="Товары быстрого выбора"
      >
        {(
          [
            ["all", "Все"],
            ["favorites", "Избранное"],
          ] as const
        ).map(([value, label]) => (
          <Button
            key={value}
            type="button"
            variant="secondary"
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
            className={cn("pos-catalog-filter flex-1", touch && "min-h-12")}
          >
            {label}
          </Button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
        {!branchId ? (
          <p className="p-6 text-center text-sm text-foreground-muted">
            Выберите аптеку для просмотра товаров.
          </p>
        ) : source.isLoading || (filter === "all" && catalog.isPlaceholderData) ? (
          <div className="grid grid-cols-1 gap-2">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-36 rounded-md" />
            ))}
          </div>
        ) : source.error ? (
          <div
            role="alert"
            className="flex min-h-48 flex-col items-center justify-center gap-3 px-6 text-center"
          >
            <p className="text-sm text-danger">
              {filter === "all"
                ? "Не удалось загрузить товары."
                : "Не удалось загрузить избранные товары."}
            </p>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => void source.refetch()}
            >
              Повторить
            </Button>
          </div>
        ) : products.length ? (
          <div className={cn("grid gap-2", view === "grid" ? "grid-cols-2" : "grid-cols-1")}>
            {products.map((item) => {
              const stock = item.stock_available == null ? null : Number(item.stock_available);
              const inactive = !item.is_active || Boolean(item.deleted_at);
              const unavailable = inactive || (!onSelect && stock !== null && stock <= 0);
              const adding = pendingId === item.id;

              return (
                <article key={item.id} className="relative min-w-0">
                  <button
                    type="button"
                    aria-label={`Выбрать ${item.brand_name}`}
                    aria-pressed={selectedCatalogId === item.id}
                    aria-busy={adding}
                    disabled={busy || unavailable || pendingId !== null}
                    onClick={() => void addProduct(item)}
                    className={cn(
                      "pos-quick-product flex w-full min-w-0 gap-3 rounded-lg border border-border bg-surface p-3 text-left transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
                      view === "grid" ? "h-full flex-col" : "items-start justify-between",
                      selectedCatalogId === item.id &&
                        "border-primary bg-primary/5 ring-1 ring-primary",
                      touch ? "pr-14" : "pr-11",
                    )}
                  >
                    <span className="w-full min-w-0 flex-1">
                      <span className="block break-words text-sm font-semibold leading-5 text-foreground">
                        {item.brand_name}
                        {item.dispensing_type === "prescription" ? " · Rx" : ""}
                      </span>
                      <span className="mt-1 block break-words text-xs text-foreground-muted">
                        {[item.dosage, item.pack_size].filter(Boolean).join(" · ") ||
                          item.form ||
                          "Лекарственный товар"}
                      </span>
                      <span
                        className={cn(
                          "mt-1 block text-xs",
                          unavailable ? "text-danger" : "text-foreground-muted",
                        )}
                      >
                        {inactive
                          ? "Снят с продажи"
                          : onSelect && (stock === null || stock <= 0)
                            ? "Остаток проверяется при выборе"
                            : stock === null
                              ? "Остаток не указан"
                              : unavailable
                                ? "Нет в наличии"
                                : `Остаток: ${formatStock(stock)} учётных ед.`}
                      </span>
                    </span>
                    <span
                      className={cn(
                        "flex shrink-0 flex-col gap-1",
                        view === "list" ? "max-w-[42%] items-end text-right" : "items-start",
                      )}
                    >
                      <span className="text-sm font-semibold tabular-nums text-foreground">
                        {item.base_price
                          ? Number(item.base_price).toFixed(2).replace(".", ",")
                          : "—"}
                      </span>
                      <span className="text-xs text-foreground-muted">
                        {item.currency} · базовая цена
                      </span>
                      {adding ? (
                        <span role="status" className="w-full text-xs text-primary">
                          Выбор…
                        </span>
                      ) : null}
                    </span>
                  </button>
                  <button
                    type="button"
                    aria-label={
                      favoriteIds.has(item.id)
                        ? `Убрать ${item.brand_name} из избранного`
                        : `Добавить ${item.brand_name} в избранное`
                    }
                    aria-pressed={favoriteIds.has(item.id)}
                    title={
                      favoriteIds.has(item.id) ? "Убрать из избранного" : "Добавить в избранное"
                    }
                    disabled={favoritePending || favorites.isLoading || Boolean(favorites.error)}
                    onClick={() => void toggleFavorite(item)}
                    className={cn(
                      "absolute right-1 top-1 grid place-items-center rounded-md text-primary hover:bg-primary/10 disabled:opacity-50",
                      touch ? "h-12 w-12" : "h-9 w-9",
                    )}
                  >
                    <StarIcon filled={favoriteIds.has(item.id)} />
                  </button>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="flex min-h-48 flex-col items-center justify-center gap-1 px-6 text-center">
            <p className="text-sm font-medium text-foreground">
              {filter === "all" ? "Товаров пока нет" : "Избранных товаров пока нет"}
            </p>
            <p className="max-w-sm text-sm text-foreground-muted">
              {filter === "all"
                ? "Добавленные в каталог товары появятся здесь."
                : "Найдите товар в строке поиска или во вкладке «Все» и нажмите звезду. Он появится здесь только для вашего аккаунта."}
            </p>
          </div>
        )}
        {filter === "all" && catalog.data && catalog.data.total > 20 ? (
          <nav
            aria-label="Страницы товаров"
            className="mt-3 flex items-center justify-between gap-2"
          >
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={page <= 1 || catalog.isFetching}
              onClick={() => setPagination({ branchId, page: page - 1 })}
            >
              Назад
            </Button>
            <span className="text-xs text-foreground-muted">
              {page} / {Math.ceil(catalog.data.total / 20)}
            </span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={page * 20 >= catalog.data.total || catalog.isFetching}
              onClick={() => setPagination({ branchId, page: page + 1 })}
            >
              Далее
            </Button>
          </nav>
        ) : null}
        {favoriteError ? (
          <p className="mt-2 text-center text-xs text-danger" role="alert">
            {favoriteError}
          </p>
        ) : null}
      </div>
    </section>
  );
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

function StarIcon({ filled }: { filled: boolean }): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="m12 2.8 2.8 5.7 6.3.9-4.6 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3-4.6-4.4 6.3-.9z" />
    </svg>
  );
}
