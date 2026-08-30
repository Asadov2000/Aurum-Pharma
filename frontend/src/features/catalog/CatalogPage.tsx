import { useEffect, useMemo, useState } from "react";

import {
  ActionMenu,
  Badge,
  Button,
  ConfigurableFilterBar,
  ConfirmDialog,
  Input,
  Label,
  Modal,
  PageHeader,
  Pagination,
  Select,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";
import { cn } from "@/lib/utils";

import { BarcodesPanel } from "./BarcodesPanel";
import { CatalogImage } from "./CatalogImage";
import { CatalogImageManager } from "./CatalogImageManager";
import { CatalogItemDetails } from "./CatalogItemDetails";
import { CatalogItemForm } from "./CatalogItemForm";
import { ImportWizard } from "./ImportWizard";
import { dispensingLabel, dispensingOptions, storageLabel, storageOptions } from "./labels";
import {
  useCatalogItemQuery,
  useCatalogQuery,
  useDeleteCatalogItem,
  useRestoreCatalogItem,
} from "./queries";
import { useCatalogSummaryQuery } from "./summaryQueries";
import { type CatalogItem, type DispensingType, type StorageType } from "./types";

type LifecycleFilter = "active" | "inactive" | "archived" | "current" | "all";
type ImageFilter = "any" | "with_image" | "without_image";
type BarcodeFilter = "any" | "with_barcode" | "without_barcode";
type ViewMode = "list" | "grid";
type Preset = "all" | "active" | "without_image" | "without_barcode" | "inactive" | "archived";

const pageSizeOptions = [25, 50, 100] as const;
const wideCatalogQuery = "(min-width: 1536px)";

function useWideCatalogLayout(): boolean {
  const [isWide, setIsWide] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.(wideCatalogQuery).matches === true,
  );

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mediaQuery = window.matchMedia(wideCatalogQuery);
    const update = (event: MediaQueryListEvent): void => setIsWide(event.matches);
    setIsWide(mediaQuery.matches);
    mediaQuery.addEventListener("change", update);
    return () => mediaQuery.removeEventListener("change", update);
  }, []);

  return isWide;
}

function itemStatus(item: CatalogItem): {
  label: string;
  tone: "neutral" | "success" | "warning";
} {
  if (item.deleted_at) return { label: "В архиве", tone: "neutral" };
  if (!item.is_active) return { label: "Не показывается в кассе", tone: "warning" };
  return { label: "Доступен для продажи", tone: "success" };
}

function formattedPrice(item: CatalogItem): string {
  return item.base_price
    ? `${Number(item.base_price).toFixed(2)} ${item.currency}`
    : "Цена не задана";
}

function SummaryMetric({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value: number | undefined;
  active?: boolean;
  onClick?: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      className={cn(
        "min-w-0 border-l-2 px-4 py-3 text-left transition-colors duration-fast hover:bg-foreground/[0.025]",
        active ? "border-primary bg-primary/5" : "border-transparent",
      )}
      onClick={onClick}
      aria-pressed={active}
    >
      <span className="block text-xs font-medium text-foreground-muted">{label}</span>
      <span className="mt-1 block text-xl font-semibold tabular-nums text-foreground">
        {value ?? "—"}
      </span>
    </button>
  );
}

function CatalogDetailContent({
  item,
  canUpdate,
  onEdit,
}: {
  item: CatalogItem;
  canUpdate: boolean;
  onEdit: (item: CatalogItem) => void;
}): JSX.Element {
  const detail = useCatalogItemQuery(item.id);
  const current = detail.data ?? item;

  return (
    <div className="space-y-4">
      {detail.error && !detail.data ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2">
          <p className="text-sm text-foreground-secondary" role="status">
            Показаны краткие данные. Полная карточка не загрузилась.
          </p>
          <Button variant="secondary" size="sm" onClick={() => void detail.refetch()}>
            Повторить
          </Button>
        </div>
      ) : null}
      <CatalogImageManager item={current} canManage={canUpdate} />
      <CatalogItemDetails item={current} />
      <BarcodesPanel itemId={current.id} canManage={canUpdate && !current.deleted_at} />
      {canUpdate && !current.deleted_at && (
        <div className="flex justify-end border-t border-border pt-4">
          <Button onClick={() => onEdit(current)}>Изменить позицию</Button>
        </div>
      )}
    </div>
  );
}

export function CatalogPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("catalog");
  const canCreate = hasPermission(user, "catalog.create");
  const canUpdate = hasPermission(user, "catalog.update");
  const canDelete = hasPermission(user, "catalog.delete");
  const canImport = canCreate && canUpdate;
  const isWideCatalogLayout = useWideCatalogLayout();

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [manufacturerInput, setManufacturerInput] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [categoryInput, setCategoryInput] = useState("");
  const [category, setCategory] = useState("");
  const [dispensing, setDispensing] = useState<DispensingType | "">("");
  const [storage, setStorage] = useState<StorageType | "">("");
  const [lifecycle, setLifecycle] = useState<LifecycleFilter>("current");
  const [imageState, setImageState] = useState<ImageFilter>("any");
  const [barcodeState, setBarcodeState] = useState<BarcodeFilter>("any");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<(typeof pageSizeOptions)[number]>(25);
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    if (typeof window === "undefined") return "list";
    return window.localStorage.getItem("aurum:catalog:view") === "grid" ? "grid" : "list";
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<CatalogItem | null>(null);
  const [editing, setEditing] = useState<CatalogItem | null>(null);
  const [importing, setImporting] = useState(false);
  const [confirmItem, setConfirmItem] = useState<CatalogItem | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setManufacturer(manufacturerInput.trim());
      setCategory(categoryInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [categoryInput, manufacturerInput, qInput]);

  const params = useMemo(
    () => ({
      q: q || undefined,
      manufacturer: manufacturer || undefined,
      category: category || undefined,
      dispensing_type: dispensing || undefined,
      storage_type: storage || undefined,
      lifecycle,
      image_state: imageState,
      barcode_state: barcodeState,
      page,
      page_size: pageSize,
    }),
    [
      barcodeState,
      category,
      dispensing,
      imageState,
      lifecycle,
      manufacturer,
      page,
      pageSize,
      q,
      storage,
    ],
  );
  const query = useCatalogQuery(params);
  const summary = useCatalogSummaryQuery();
  const deleteMutation = useDeleteCatalogItem();
  const restoreMutation = useRestoreCatalogItem();
  const rows = useMemo(() => query.data?.items ?? [], [query.data?.items]);
  const isShowingPreviousResults = query.isPlaceholderData && query.isFetching;
  const total = query.data?.total ?? 0;
  const selectedItem = rows.find((item) => item.id === selectedId) ?? rows[0] ?? null;
  const activePreset: Preset | null =
    lifecycle === "current" && imageState === "any" && barcodeState === "any"
      ? "all"
      : lifecycle === "current" && imageState === "without_image" && barcodeState === "any"
        ? "without_image"
        : lifecycle === "current" && imageState === "any" && barcodeState === "without_barcode"
          ? "without_barcode"
          : lifecycle === "active" && imageState === "any" && barcodeState === "any"
            ? "active"
            : lifecycle === "inactive" && imageState === "any" && barcodeState === "any"
              ? "inactive"
              : lifecycle === "archived" && imageState === "any" && barcodeState === "any"
                ? "archived"
                : null;
  const hasFilters = Boolean(
    qInput.trim() ||
    manufacturerInput.trim() ||
    categoryInput.trim() ||
    dispensing ||
    storage ||
    lifecycle !== "current" ||
    imageState !== "any" ||
    barcodeState !== "any",
  );

  useEffect(() => {
    if (!query.data) return;
    const lastPage = Math.max(1, Math.ceil(query.data.total / pageSize));
    if (page > lastPage) setPage(lastPage);
  }, [page, pageSize, query.data]);

  useEffect(() => {
    if (rows.length === 0) {
      setSelectedId(null);
      return;
    }
    if (!rows.some((item) => item.id === selectedId)) setSelectedId(rows[0]?.id ?? null);
  }, [rows, selectedId]);

  const resetFilters = () => {
    setQInput("");
    setQ("");
    setManufacturerInput("");
    setManufacturer("");
    setCategoryInput("");
    setCategory("");
    setDispensing("");
    setStorage("");
    setLifecycle("current");
    setImageState("any");
    setBarcodeState("any");
    setPage(1);
  };

  const applyPreset = (preset: Preset) => {
    setImageState(preset === "without_image" ? "without_image" : "any");
    setBarcodeState(preset === "without_barcode" ? "without_barcode" : "any");
    setLifecycle(
      preset === "active"
        ? "active"
        : preset === "inactive"
          ? "inactive"
          : preset === "archived"
            ? "archived"
            : "current",
    );
    setPage(1);
  };

  const changeViewMode = (mode: ViewMode) => {
    setViewMode(mode);
    try {
      window.localStorage.setItem("aurum:catalog:view", mode);
    } catch {
      // View preference is optional; the catalog remains usable without storage.
    }
  };

  const openItem = (item: CatalogItem) => {
    if (isWideCatalogLayout) {
      setSelectedId(item.id);
      return;
    }
    setViewing(item);
  };

  const doArchive = async () => {
    if (!confirmItem) return;
    setActionError(null);
    try {
      await deleteMutation.mutateAsync(confirmItem.id);
      setConfirmItem(null);
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось архивировать позицию"));
    }
  };

  const doRestore = async (item: CatalogItem) => {
    setActionError(null);
    try {
      await restoreMutation.mutateAsync(item.id);
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось восстановить позицию"));
    }
  };

  const rowActions = (item: CatalogItem) => [
    { label: "Открыть", onSelect: () => setViewing(item) },
    ...(!item.deleted_at && canUpdate
      ? [{ label: "Изменить", onSelect: () => setEditing(item) }]
      : []),
    ...(!item.deleted_at && canDelete
      ? [
          {
            label: "Архивировать",
            onSelect: () => setConfirmItem(item),
            tone: "danger" as const,
          },
        ]
      : []),
    ...(item.deleted_at && canDelete
      ? [{ label: "Восстановить", onSelect: () => void doRestore(item) }]
      : []),
  ];

  const importStorageKey = `aurum:catalog-import:v1:${user?.active_tenant_id ?? "none"}:${user?.id ?? "unknown"}`;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Каталог"
        showTitleOnDesktop
        description="Справочник товаров аптеки: названия, цены, условия отпуска, хранение и штрихкоды."
        meta={isShowingPreviousResults ? "Поиск…" : query.isFetching ? "Обновление…" : undefined}
        actions={
          canCreate || canImport ? (
            <>
              {canImport && (
                <Button variant="secondary" onClick={() => setImporting(true)}>
                  Загрузить из Excel/CSV
                </Button>
              )}
              {canCreate && <Button onClick={() => setCreating(true)}>Добавить товар</Button>}
            </>
          ) : undefined
        }
      />

      <section
        className="grid grid-cols-2 overflow-hidden rounded-lg border border-border bg-surface sm:grid-cols-4"
        aria-label="Сводка по каталогу"
      >
        <SummaryMetric
          label="Всего товаров"
          value={summary.data?.total}
          active={activePreset === "all"}
          onClick={() => applyPreset("all")}
        />
        <SummaryMetric
          label="Доступны для продажи"
          value={summary.data?.active}
          active={activePreset === "active"}
          onClick={() => applyPreset("active")}
        />
        <SummaryMetric
          label="Без штрихкода"
          value={summary.data?.without_barcode}
          active={activePreset === "without_barcode"}
          onClick={() => applyPreset("without_barcode")}
        />
        <SummaryMetric
          label="Без фото"
          value={summary.data?.without_image}
          active={activePreset === "without_image"}
          onClick={() => applyPreset("without_image")}
        />
      </section>
      {summary.error ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2">
          <p className="text-sm text-foreground-secondary" role="status">
            Сводка временно недоступна. Список товаров продолжает работать.
          </p>
          <Button variant="secondary" size="sm" onClick={() => void summary.refetch()}>
            Повторить
          </Button>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 border-b border-border">
        <nav
          className="flex min-w-0 max-w-full gap-1 overflow-x-auto"
          aria-label="Представления каталога"
        >
          {(
            [
              ["all", "Все товары", summary.data?.total],
              ["without_image", "Без фото", summary.data?.without_image],
              ["without_barcode", "Без штрихкода", summary.data?.without_barcode],
              ["inactive", "Отключённые", summary.data?.inactive],
              ["archived", "Архив", summary.data?.archived],
            ] as const
          ).map(([id, label, count]) => (
            <button
              key={id}
              type="button"
              className={cn(
                "min-h-10 shrink-0 border-b-2 px-3 text-sm font-medium transition-colors duration-fast",
                activePreset === id
                  ? "border-primary text-primary"
                  : "border-transparent text-foreground-secondary hover:text-foreground",
              )}
              onClick={() => applyPreset(id)}
              aria-pressed={activePreset === id}
            >
              {label}
              {count !== undefined && (
                <span className="ml-1.5 text-xs tabular-nums text-foreground-muted">{count}</span>
              )}
            </button>
          ))}
        </nav>
        <div
          className="mb-2 flex rounded-md border border-border bg-surface p-0.5"
          aria-label="Вид каталога"
        >
          <button
            type="button"
            className={cn(
              "grid h-8 w-9 place-items-center rounded text-base",
              viewMode === "list" ? "bg-primary text-primary-foreground" : "text-foreground-muted",
            )}
            aria-label="Список"
            aria-pressed={viewMode === "list"}
            title="Список"
            onClick={() => changeViewMode("list")}
          >
            ☰
          </button>
          <button
            type="button"
            className={cn(
              "grid h-8 w-9 place-items-center rounded text-base",
              viewMode === "grid" ? "bg-primary text-primary-foreground" : "text-foreground-muted",
            )}
            aria-label="Плитка"
            aria-pressed={viewMode === "grid"}
            title="Плитка"
            onClick={() => changeViewMode("grid")}
          >
            ▦
          </button>
        </div>
      </div>

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div className="w-full sm:w-80">
                <Label htmlFor="catalog_search">Поиск</Label>
                <Input
                  id="catalog_search"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Название, МНН или штрихкод"
                  autoComplete="off"
                />
              </div>
            ),
            active: Boolean(qInput.trim()),
            onClear: () => {
              setQInput("");
              setQ("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "lifecycle",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="catalog_lifecycle">Статус</Label>
                <Select
                  id="catalog_lifecycle"
                  value={lifecycle}
                  onChange={(event) => {
                    setLifecycle(event.target.value as LifecycleFilter);
                    setPage(1);
                  }}
                  className="w-full sm:w-48"
                >
                  <option value="current">Все товары, кроме архива</option>
                  <option value="active">Доступны для продажи</option>
                  <option value="inactive">Не показываются в кассе</option>
                  <option value="archived">Архив</option>
                  <option value="all">Все товары, включая архив</option>
                </Select>
              </div>
            ),
            active: lifecycle !== "current",
            onClear: () => {
              setLifecycle("current");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "dispensing",
            label: "Условия отпуска",
            content: (
              <div>
                <Label htmlFor="catalog_dispensing">Условия отпуска</Label>
                <Select
                  id="catalog_dispensing"
                  value={dispensing}
                  onChange={(event) => {
                    setDispensing(event.target.value as DispensingType | "");
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                >
                  <option value="">Все</option>
                  {dispensingOptions.map((option) => (
                    <option key={option} value={option}>
                      {dispensingLabel[option]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(dispensing),
            onClear: () => {
              setDispensing("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "image",
            label: "Фотография",
            content: (
              <div>
                <Label htmlFor="catalog_image">Фотография</Label>
                <Select
                  id="catalog_image"
                  value={imageState}
                  onChange={(event) => {
                    setImageState(event.target.value as ImageFilter);
                    setPage(1);
                  }}
                  className="w-full sm:w-44"
                >
                  <option value="any">Не важно</option>
                  <option value="with_image">С фото</option>
                  <option value="without_image">Без фото</option>
                </Select>
              </div>
            ),
            active: imageState !== "any",
            onClear: () => {
              setImageState("any");
              setPage(1);
            },
          },
          {
            id: "barcode",
            label: "Штрихкод",
            content: (
              <div>
                <Label htmlFor="catalog_barcode">Штрихкод</Label>
                <Select
                  id="catalog_barcode"
                  value={barcodeState}
                  onChange={(event) => {
                    setBarcodeState(event.target.value as BarcodeFilter);
                    setPage(1);
                  }}
                  className="w-full sm:w-48"
                >
                  <option value="any">Не важно</option>
                  <option value="with_barcode">Есть</option>
                  <option value="without_barcode">Не указан</option>
                </Select>
              </div>
            ),
            active: barcodeState !== "any",
            onClear: () => {
              setBarcodeState("any");
              setPage(1);
            },
          },
          {
            id: "manufacturer",
            label: "Производитель",
            content: (
              <div>
                <Label htmlFor="catalog_manufacturer">Производитель</Label>
                <Input
                  id="catalog_manufacturer"
                  value={manufacturerInput}
                  onChange={(event) => setManufacturerInput(event.target.value)}
                  placeholder="Название компании"
                  autoComplete="off"
                  className="w-full sm:w-56"
                />
              </div>
            ),
            active: Boolean(manufacturerInput.trim()),
            onClear: () => {
              setManufacturerInput("");
              setManufacturer("");
              setPage(1);
            },
          },
          {
            id: "category",
            label: "Категория",
            content: (
              <div>
                <Label htmlFor="catalog_category">Категория</Label>
                <Input
                  id="catalog_category"
                  value={categoryInput}
                  onChange={(event) => setCategoryInput(event.target.value)}
                  placeholder="Например, обезболивающие"
                  autoComplete="off"
                  className="w-full sm:w-56"
                />
              </div>
            ),
            active: Boolean(categoryInput.trim()),
            onClear: () => {
              setCategoryInput("");
              setCategory("");
              setPage(1);
            },
          },
          {
            id: "storage",
            label: "Хранение",
            content: (
              <div>
                <Label htmlFor="catalog_storage">Хранение</Label>
                <Select
                  id="catalog_storage"
                  value={storage}
                  onChange={(event) => {
                    setStorage(event.target.value as StorageType | "");
                    setPage(1);
                  }}
                  className="w-full sm:w-48"
                >
                  <option value="">Все условия</option>
                  {storageOptions.map((option) => (
                    <option key={option} value={option}>
                      {storageLabel[option]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(storage),
            onClear: () => {
              setStorage("");
              setPage(1);
            },
          },
        ]}
        onResetValues={resetFilters}
      />

      {actionError && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2"
          role="alert"
        >
          <span className="text-sm text-danger-foreground">{actionError}</span>
          <Button variant="ghost" size="sm" onClick={() => setActionError(null)}>
            Закрыть
          </Button>
        </div>
      )}

      {isShowingPreviousResults ? (
        <div
          className="rounded-lg border border-info/30 bg-info-subtle px-3 py-2 text-sm text-info-foreground"
          role="status"
        >
          Ищем товары. Пока показан предыдущий список; действия временно недоступны.
        </div>
      ) : null}

      {query.error && !query.data ? (
        <TableEmpty
          title="Каталог не загрузился"
          action={<Button onClick={() => void query.refetch()}>Повторить</Button>}
        >
          {describeApiError(query.error, "Проверьте соединение и повторите попытку")}
        </TableEmpty>
      ) : query.isLoading ? (
        <SkeletonRows rows={7} />
      ) : rows.length === 0 ? (
        <TableEmpty
          title={hasFilters ? "Ничего не найдено" : "Каталог пуст"}
          action={
            hasFilters ? (
              <Button variant="secondary" onClick={resetFilters}>
                Сбросить фильтры
              </Button>
            ) : canCreate || canImport ? (
              <div className="flex flex-wrap justify-center gap-2">
                {canImport && (
                  <Button variant="secondary" onClick={() => setImporting(true)}>
                    Загрузить из Excel/CSV
                  </Button>
                )}
                {canCreate && <Button onClick={() => setCreating(true)}>Добавить товар</Button>}
              </div>
            ) : undefined
          }
        >
          {hasFilters
            ? "Измените запрос или верните стандартные фильтры."
            : canCreate || canImport
              ? "Добавьте первый товар вручную или загрузите список из Excel/CSV."
              : "В аптеке пока нет доступных товаров."}
        </TableEmpty>
      ) : (
        <>
          {query.error && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2">
              <p className="text-sm text-foreground-secondary" role="status">
                Показаны ранее загруженные данные. Обновление не удалось.
              </p>
              <Button variant="secondary" size="sm" onClick={() => void query.refetch()}>
                Повторить
              </Button>
            </div>
          )}
          <div className="grid min-w-0 gap-4 2xl:grid-cols-[minmax(0,1fr)_360px]">
            <section
              className={cn("min-w-0", isShowingPreviousResults && "opacity-70")}
              aria-label="Товары каталога"
              aria-busy={isShowingPreviousResults}
            >
              {viewMode === "list" ? (
                <div className="overflow-hidden rounded-lg border border-border bg-surface">
                  <div className="hidden grid-cols-[64px_minmax(220px,1.4fr)_minmax(140px,.8fr)_150px_180px_120px_44px] gap-3 border-b border-border bg-background px-3 py-2 text-xs font-semibold text-foreground-muted xl:grid">
                    <span className="sr-only">Фото</span>
                    <span>Товар</span>
                    <span>Производитель</span>
                    <span>Условия отпуска</span>
                    <span>Статус</span>
                    <span>Цена</span>
                    <span className="sr-only">Действия</span>
                  </div>
                  <div className="divide-y divide-border">
                    {rows.map((item) => {
                      const status = itemStatus(item);
                      const selected = item.id === selectedItem?.id;
                      return (
                        <article
                          key={item.id}
                          className={cn(
                            "relative flex min-w-0 items-stretch transition-colors duration-fast",
                            selected ? "bg-primary/5" : "hover:bg-foreground/[0.02]",
                          )}
                        >
                          {selected && (
                            <span className="absolute inset-y-0 left-0 w-0.5 bg-primary" />
                          )}
                          <button
                            type="button"
                            className="grid min-w-0 flex-1 grid-cols-[64px_minmax(0,1fr)] items-center gap-3 px-3 py-3 text-left disabled:cursor-wait xl:grid-cols-[64px_minmax(220px,1.4fr)_minmax(140px,.8fr)_150px_180px_120px]"
                            aria-label={`Открыть карточку ${item.brand_name}`}
                            aria-pressed={isWideCatalogLayout ? selected : undefined}
                            disabled={isShowingPreviousResults}
                            onClick={() => openItem(item)}
                            onDoubleClick={() => isWideCatalogLayout && setViewing(item)}
                          >
                            <CatalogImage item={item} />
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-semibold text-foreground">
                                {item.brand_name}
                              </span>
                              <span className="mt-0.5 block truncate text-xs text-foreground-muted">
                                {item.inn ||
                                  [item.form, item.dosage].filter(Boolean).join(" · ") ||
                                  "Сведения не указаны"}
                              </span>
                              <span className="mt-1 flex flex-wrap gap-1 xl:hidden">
                                <Badge tone={status.tone}>{status.label}</Badge>
                                <span className="text-xs font-semibold text-foreground">
                                  {formattedPrice(item)}
                                </span>
                              </span>
                            </span>
                            <span className="hidden truncate text-sm text-foreground-secondary xl:block">
                              {item.manufacturer || "—"}
                            </span>
                            <span className="hidden text-sm text-foreground-secondary xl:block">
                              {dispensingLabel[item.dispensing_type]}
                            </span>
                            <span className="hidden xl:block">
                              <Badge tone={status.tone}>{status.label}</Badge>
                            </span>
                            <span className="hidden whitespace-nowrap text-sm font-semibold text-foreground xl:block">
                              {formattedPrice(item)}
                            </span>
                          </button>
                          <div className="grid w-11 shrink-0 place-items-center pr-1">
                            <ActionMenu
                              label={`Действия для ${item.brand_name}`}
                              items={rowActions(item)}
                              isLoading={isShowingPreviousResults}
                            />
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-2">
                  {rows.map((item) => {
                    const status = itemStatus(item);
                    const selected = item.id === selectedItem?.id;
                    return (
                      <article
                        key={item.id}
                        className={cn(
                          "relative overflow-hidden rounded-lg border bg-surface",
                          selected ? "border-primary ring-1 ring-primary/20" : "border-border",
                        )}
                      >
                        <button
                          type="button"
                          className="block w-full p-3 text-left"
                          aria-label={`Открыть карточку ${item.brand_name}`}
                          aria-pressed={isWideCatalogLayout ? selected : undefined}
                          disabled={isShowingPreviousResults}
                          onClick={() => openItem(item)}
                          onDoubleClick={() => isWideCatalogLayout && setViewing(item)}
                        >
                          <CatalogImage item={item} variant="detail" className="h-32" />
                          <span className="mt-3 block truncate text-sm font-semibold text-foreground">
                            {item.brand_name}
                          </span>
                          <span className="mt-1 block truncate text-xs text-foreground-muted">
                            {item.manufacturer || item.inn || "Сведения не указаны"}
                          </span>
                          <span className="mt-3 flex items-center justify-between gap-2">
                            <Badge tone={status.tone}>{status.label}</Badge>
                            <span className="text-sm font-semibold text-foreground">
                              {formattedPrice(item)}
                            </span>
                          </span>
                        </button>
                        <div className="absolute right-2 top-2 rounded-md bg-surface/90">
                          <ActionMenu
                            label={`Действия для ${item.brand_name}`}
                            items={rowActions(item)}
                            isLoading={isShowingPreviousResults}
                          />
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
            {selectedItem && isWideCatalogLayout && (
              <aside className="hidden 2xl:block" aria-label="Карточка выбранной позиции">
                <div className="sticky top-4 max-h-[calc(100vh-7rem)] overflow-y-auto rounded-lg border border-border bg-surface p-4 shadow-sm">
                  <CatalogDetailContent
                    item={selectedItem}
                    canUpdate={canUpdate}
                    onEdit={setEditing}
                  />
                </div>
              </aside>
            )}
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="flex items-center gap-2 text-sm">
              <Label htmlFor="catalog_page_size" className="mb-0 whitespace-nowrap">
                На странице
              </Label>
              <Select
                id="catalog_page_size"
                value={pageSize}
                onChange={(event) => {
                  setPageSize(Number(event.target.value) as (typeof pageSizeOptions)[number]);
                  setPage(1);
                }}
                className="w-20"
              >
                {pageSizeOptions.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </Select>
            </div>
            <div className="min-w-0 flex-1">
              <Pagination page={page} pageSize={pageSize} total={total} onPage={setPage} />
            </div>
          </div>
        </>
      )}

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="Добавить товар"
        className="max-w-2xl"
      >
        <CatalogItemForm item={null} onClose={() => setCreating(false)} />
      </Modal>
      <Modal
        open={viewing !== null}
        onClose={() => setViewing(null)}
        title="Карточка товара"
        className="max-w-2xl"
      >
        {viewing && (
          <CatalogDetailContent
            item={viewing}
            canUpdate={canUpdate}
            onEdit={(item) => {
              setViewing(null);
              setEditing(item);
            }}
          />
        )}
      </Modal>
      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing ? `Изменить товар: ${editing.brand_name}` : ""}
        className="max-w-2xl"
      >
        {editing && (
          <div className="space-y-4">
            <CatalogItemForm item={editing} onClose={() => setEditing(null)} />
            <CatalogImageManager item={editing} canManage={canUpdate} />
            <BarcodesPanel itemId={editing.id} canManage={canUpdate} />
          </div>
        )}
      </Modal>
      <Modal
        open={importing}
        onClose={() => setImporting(false)}
        title="Загрузить товары из Excel/CSV"
        className="max-w-3xl"
      >
        <ImportWizard
          onClose={() => setImporting(false)}
          canRollback={canDelete}
          storageKey={importStorageKey}
        />
      </Modal>
      <ConfirmDialog
        open={confirmItem !== null}
        title="Перенести товар в архив?"
        message={
          <>
            «{confirmItem?.brand_name}» больше нельзя будет выбирать в кассе и новых документах.
            История продаж и складских операций сохранится. Товар можно восстановить через раздел
            «Архив».
            {actionError && <span className="mt-2 block text-danger">{actionError}</span>}
          </>
        }
        confirmLabel="Архивировать"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => void doArchive()}
        onCancel={() => {
          setConfirmItem(null);
          setActionError(null);
        }}
      />
    </div>
  );
}
