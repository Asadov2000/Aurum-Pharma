import { useEffect, useMemo, useState } from "react";

import { Badge, Button, Input, Label, PageHeader, Pagination, SkeletonRows } from "@/components/ui";
import { type CatalogItem } from "@/features/catalog/types";
import { describeApiError } from "@/features/foundation/errors";
import { type BatchWithExpiry } from "@/features/inventory/types";
import { type Supplier } from "@/features/suppliers/types";

import { type DataPlane } from "./dataPlane";
import {
  useEdgeCatalogItemQuery,
  useEdgeCatalogQuery,
  useEdgeInventoryQuery,
  useEdgeSuppliersQuery,
} from "./queries";
import { EdgeSnapshotBanner } from "./EdgeSnapshotBanner";

const PAGE_SIZE = 50;

export function EdgeCatalogPage({ plane }: { readonly plane: EdgePlane }): JSX.Element {
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(queryInput.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [queryInput]);

  const params = useMemo(
    () => ({ branchId: plane.branchId, page, pageSize: PAGE_SIZE, query: query || undefined }),
    [page, plane.branchId, query],
  );
  const list = useEdgeCatalogQuery(params);
  const rows = useMemo(() => list.data?.items ?? [], [list.data?.items]);

  useEffect(() => {
    if (rows.length === 0) setSelectedId(null);
    else if (!rows.some((item) => item.id === selectedId)) setSelectedId(rows[0]?.id ?? null);
  }, [rows, selectedId]);

  const details = useEdgeCatalogItemQuery(plane.branchId, selectedId);
  const selected = details.data?.item ?? rows.find((item) => item.id === selectedId) ?? null;

  return (
    <EdgePage title="Каталог" description="Последний проверенный каталог этой аптечной точки.">
      <SearchField
        id="edge_catalog_search"
        label="Поиск"
        placeholder="Название, МНН или штрихкод"
        value={queryInput}
        onChange={setQueryInput}
      />
      <EdgeQueryState
        loading={list.isLoading}
        error={list.error}
        hasData={Boolean(list.data)}
        onRetry={() => void list.refetch()}
      />
      {list.data ? (
        <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(19rem,0.42fr)]">
          <div className="overflow-hidden rounded-lg border border-border bg-surface">
            {rows.length ? (
              rows.map((item) => (
                <CatalogRow
                  key={item.id}
                  item={item}
                  selected={item.id === selectedId}
                  onSelect={() => setSelectedId(item.id)}
                />
              ))
            ) : (
              <EmptyState text="По выбранному запросу товары не найдены." />
            )}
          </div>
          <div className="rounded-lg border border-border bg-surface p-4">
            {selected ? <CatalogDetails item={selected} /> : <EmptyState text="Выберите товар." />}
          </div>
        </div>
      ) : null}
      {list.data ? (
        <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
      ) : null}
    </EdgePage>
  );
}

export function EdgeSuppliersPage({ plane }: { readonly plane: EdgePlane }): JSX.Element {
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(queryInput.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [queryInput]);
  const params = useMemo(
    () => ({ branchId: plane.branchId, page, pageSize: PAGE_SIZE, query: query || undefined }),
    [page, plane.branchId, query],
  );
  const result = useEdgeSuppliersQuery(params);

  return (
    <EdgePage title="Поставщики" description="Контакты поставщиков из локального снимка.">
      <SearchField
        id="edge_supplier_search"
        label="Поиск"
        placeholder="Название, контакт или ИНН"
        value={queryInput}
        onChange={setQueryInput}
      />
      <EdgeQueryState
        loading={result.isLoading}
        error={result.error}
        hasData={Boolean(result.data)}
        onRetry={() => void result.refetch()}
      />
      {result.data ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {result.data.items.length ? (
            result.data.items.map((supplier) => (
              <SupplierCard key={supplier.id} supplier={supplier} />
            ))
          ) : (
            <EmptyState text="Поставщики не найдены." />
          )}
        </div>
      ) : null}
      {result.data ? (
        <Pagination page={page} pageSize={PAGE_SIZE} total={result.data.total} onPage={setPage} />
      ) : null}
    </EdgePage>
  );
}

export function EdgeInventoryPage({ plane }: { readonly plane: EdgePlane }): JSX.Element {
  const [page, setPage] = useState(1);
  const [showEmpty, setShowEmpty] = useState(false);
  const params = useMemo(
    () => ({ branchId: plane.branchId, page, pageSize: PAGE_SIZE, showEmpty }),
    [page, plane.branchId, showEmpty],
  );
  const result = useEdgeInventoryQuery(params);

  return (
    <EdgePage title="Партии" description="Остатки и сроки годности из локального снимка.">
      <label className="inline-flex min-h-11 items-center gap-2 rounded-md border border-border bg-surface px-3 text-sm">
        <input
          type="checkbox"
          checked={showEmpty}
          onChange={(event) => {
            setShowEmpty(event.target.checked);
            setPage(1);
          }}
        />
        Показывать партии без остатка
      </label>
      <EdgeQueryState
        loading={result.isLoading}
        error={result.error}
        hasData={Boolean(result.data)}
        onRetry={() => void result.refetch()}
      />
      {result.data ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Остаток" value={formatQuantity(result.data.summary.total_qty)} />
            <Metric label="Требуют внимания" value={String(result.data.summary.attention_count)} />
            <Metric label="Просрочены" value={String(result.data.summary.expired_count)} />
            <Metric label="Заблокированы" value={String(result.data.summary.blocked_count)} />
          </div>
          <div className="overflow-hidden rounded-lg border border-border bg-surface">
            {result.data.items.length ? (
              result.data.items.map((batch) => <InventoryRow key={batch.id} batch={batch} />)
            ) : (
              <EmptyState text="Партии не найдены." />
            )}
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={result.data.total} onPage={setPage} />
        </>
      ) : null}
    </EdgePage>
  );
}

type EdgePlane = Extract<DataPlane, { readonly mode: "edge-readonly" }>;

function EdgePage({
  title,
  description,
  children,
}: {
  readonly title: string;
  readonly description: string;
  readonly children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="space-y-4">
      <EdgeSnapshotBanner />
      <PageHeader
        title={title}
        description={description}
        showTitleOnDesktop
        meta={<Badge tone="info">Локальный снимок</Badge>}
      />
      <div className="rounded-lg border border-info/30 bg-info-subtle px-4 py-3 text-sm text-info-foreground">
        Доступен безопасный просмотр. Создание, изменение и удаление будут включены после отдельной
        проверки Edge-команд.
      </div>
      {children}
    </div>
  );
}

function SearchField({
  id,
  label,
  placeholder,
  value,
  onChange,
}: {
  readonly id: string;
  readonly label: string;
  readonly placeholder: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
}): JSX.Element {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function EdgeQueryState({
  loading,
  error,
  hasData,
  onRetry,
}: {
  readonly loading: boolean;
  readonly error: unknown;
  readonly hasData: boolean;
  readonly onRetry: () => void;
}): JSX.Element | null {
  if (loading && !hasData) return <SkeletonRows rows={6} />;
  if (!error) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3">
      <p className="text-sm text-danger-foreground" role="alert">
        {hasData
          ? "Обновить локальные данные не удалось. Показан предыдущий результат."
          : describeApiError(error, "Локальный снимок пока недоступен.")}
      </p>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        Повторить
      </Button>
    </div>
  );
}

function CatalogRow({
  item,
  selected,
  onSelect,
}: {
  readonly item: CatalogItem;
  readonly selected: boolean;
  readonly onSelect: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      aria-pressed={selected}
      className={`grid w-full gap-1 border-b border-border px-4 py-3 text-left last:border-b-0 hover:bg-foreground/[0.025] ${selected ? "bg-primary/5" : ""}`}
      onClick={onSelect}
    >
      <span className="font-semibold text-foreground">{item.brand_name}</span>
      <span className="text-sm text-foreground-secondary">
        {[item.inn, item.manufacturer].filter(Boolean).join(" · ") || "Сведения не указаны"}
      </span>
      <span className="text-sm font-medium text-foreground">
        {item.base_price
          ? `${Number(item.base_price).toFixed(2)} ${item.currency}`
          : "Цена не задана"}
      </span>
    </button>
  );
}

function CatalogDetails({ item }: { readonly item: CatalogItem }): JSX.Element {
  const details = item as CatalogItem & {
    readonly barcodes?: readonly { readonly code: string }[];
  };
  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-foreground">{item.brand_name}</h2>
        <p className="text-sm text-foreground-secondary">{item.inn || "МНН не указано"}</p>
      </div>
      <Detail label="Производитель" value={item.manufacturer} />
      <Detail label="Форма" value={item.form} />
      <Detail label="Дозировка" value={item.dosage} />
      <Detail label="Упаковка" value={item.pack_size} />
      <Detail
        label="Штрихкоды"
        value={details.barcodes?.map((barcode) => barcode.code).join(", ") || null}
      />
    </div>
  );
}

function SupplierCard({ supplier }: { readonly supplier: Supplier }): JSX.Element {
  return (
    <article className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 className="font-semibold text-foreground">{supplier.name}</h2>
        <Badge tone={supplier.is_active ? "success" : "neutral"}>
          {supplier.is_active ? "Активен" : "Отключён"}
        </Badge>
      </div>
      <div className="mt-3 space-y-2">
        <Detail label="ИНН" value={supplier.inn_or_tin} />
        <Detail label="Контакт" value={supplier.contact_person} />
        <Detail label="Телефон" value={supplier.phone} />
        <Detail label="Email" value={supplier.email} />
      </div>
    </article>
  );
}

function InventoryRow({ batch }: { readonly batch: BatchWithExpiry }): JSX.Element {
  return (
    <article className="grid gap-2 border-b border-border px-4 py-3 last:border-b-0 sm:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_auto] sm:items-center">
      <div className="min-w-0">
        <h2 className="truncate font-semibold text-foreground">{batch.catalog_name}</h2>
        <p className="truncate text-sm text-foreground-secondary">
          {batch.batch_number ? `Партия ${batch.batch_number}` : "Без номера партии"}
        </p>
      </div>
      <div className="text-sm text-foreground-secondary">
        <div>{batch.branch_name}</div>
        <div>Годен до {formatDate(batch.expires_at)}</div>
      </div>
      <div className="text-left sm:text-right">
        <div className="font-semibold tabular-nums text-foreground">
          {formatQuantity(batch.qty_remaining)} ед.
        </div>
        <Badge tone={batch.expiry_status === "normal" ? "success" : "warning"}>
          {batch.is_blocked ? "Заблокирована" : expiryLabel(batch.expiry_status)}
        </Badge>
      </div>
    </article>
  );
}

function Metric({ label, value }: { readonly label: string; readonly value: string }): JSX.Element {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <span className="text-xs text-foreground-muted">{label}</span>
      <strong className="mt-1 block text-xl tabular-nums text-foreground">{value}</strong>
    </div>
  );
}

function Detail({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string | null;
}): JSX.Element {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-2 text-sm">
      <span className="text-foreground-muted">{label}</span>
      <span className="break-words text-foreground">{value || "—"}</span>
    </div>
  );
}

function EmptyState({ text }: { readonly text: string }): JSX.Element {
  return <p className="p-6 text-sm text-foreground-muted">{text}</p>;
}

function formatDate(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("ru-RU").format(date);
}

function formatQuantity(value: string): string {
  const quantity = Number(value);
  return Number.isFinite(quantity) ? new Intl.NumberFormat("ru-RU").format(quantity) : "—";
}

function expiryLabel(status: BatchWithExpiry["expiry_status"]): string {
  if (status === "expired") return "Просрочена";
  if (status === "red") return "Критический срок";
  if (status === "orange") return "Скоро истекает";
  if (status === "yellow") return "Требует внимания";
  return "Нормальный срок";
}
