import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
  Modal,
  PageHeader,
  Pagination,
  SegmentedControl,
  Select,
  SkeletonRows,
  Switch,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { CatalogPicker } from "@/features/catalog/CatalogPicker";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";
import { cn } from "@/lib/utils";

import { BatchDetailModal } from "./BatchDetailModal";
import {
  expiryHint,
  formatInventoryDate,
  formatInventoryMoney,
  formatInventoryQuantity,
  productSubtitle,
} from "./formatters";
import { expiryLabel, expiryOptions, expiryTone } from "./labels";
import { useBatchesQuery } from "./queries";
import { type BatchWithExpiry, type ExpiryStatus } from "./types";

const PAGE_SIZE = 25;
const CustomerReturnsPanel = lazy(() =>
  import("@/features/customerReturns/CustomerReturnsPanel").then((module) => ({
    default: module.CustomerReturnsPanel,
  })),
);

type BlockedFilter = "" | "active" | "blocked";
type BatchView = "table" | "cards";
type InventorySection = "saleable" | "customer_returns";

const inventorySections = [
  { value: "saleable", label: "Продаваемые партии" },
  { value: "customer_returns", label: "Возвраты покупателей" },
] as const;

const VIEW_STORAGE_KEY = "aurum:batches:view:v1";

export function BatchesPage(): JSX.Element {
  const { user } = useAuth();
  const canViewCustomerReturns = hasPermission(user, "customer_returns.view");
  const [section, setSection] = useState<InventorySection>("saleable");
  const filterPreferenceKey = useFilterPreferenceKey("batches");
  const [batchNumberInput, setBatchNumberInput] = useState("");
  const [batchNumber, setBatchNumber] = useState("");
  const [branchId, setBranchId] = useState("");
  const [catalogId, setCatalogId] = useState("");
  const [expiry, setExpiry] = useState<ExpiryStatus | "">("");
  const [blockedFilter, setBlockedFilter] = useState<BlockedFilter>("");
  const [showEmpty, setShowEmpty] = useState(false);
  const [page, setPage] = useState(1);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);
  const [openBatchId, setOpenBatchId] = useState<string | null>(null);
  const [view, setView] = useState<BatchView>(readBatchView);
  const isDesktopLayout = useMediaQuery("(min-width: 768px)");
  const isSplitLayout = useMediaQuery("(min-width: 1280px)");

  const branches = useBranchesQuery(true);
  const branchFilterName = branches.data?.find((branch) => branch.id === branchId)?.name;

  useEffect(() => {
    if (!canViewCustomerReturns && section !== "saleable") setSection("saleable");
  }, [canViewCustomerReturns, section]);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setBatchNumber(batchNumberInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timeout);
  }, [batchNumberInput]);

  const params = useMemo(
    () => ({
      branch_id: branchId || undefined,
      catalog_id: catalogId || undefined,
      expiry_status: expiry || undefined,
      batch_number: batchNumber || undefined,
      is_blocked:
        blockedFilter === "blocked" ? true : blockedFilter === "active" ? false : undefined,
      show_empty: showEmpty,
      page,
      page_size: PAGE_SIZE,
    }),
    [batchNumber, blockedFilter, branchId, catalogId, expiry, page, showEmpty],
  );
  const { data, isLoading, isFetching, isPlaceholderData, error, refetch } =
    useBatchesQuery(params);
  const isShowingPreviousResults = isPlaceholderData && isFetching;
  const filtersActive = Boolean(
    batchNumberInput || branchId || catalogId || expiry || blockedFilter || showEmpty,
  );
  const rows = data?.items ?? [];
  const selectedBatch = rows.find((item) => item.id === selectedBatchId) ?? rows[0] ?? null;
  const modalBatch = rows.find((item) => item.id === openBatchId);
  const showSplitWorkspace = isSplitLayout && view === "table";

  const resetFilters = () => {
    setBatchNumberInput("");
    setBatchNumber("");
    setBranchId("");
    setCatalogId("");
    setExpiry("");
    setBlockedFilter("");
    setShowEmpty(false);
    setPage(1);
  };

  const changeView = (next: BatchView) => {
    setView(next);
    writeBatchView(next);
  };

  const openBatch = (batch: BatchWithExpiry) => {
    if (isShowingPreviousResults) return;
    if (showSplitWorkspace) {
      setSelectedBatchId(batch.id);
      return;
    }
    setOpenBatchId(batch.id);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Партии"
        description="Остатки, сроки годности и движения товара по аптечным точкам."
        showTitleOnDesktop
        meta={
          data ? (
            <span aria-live="polite">
              {data.total} партий{isFetching && !isLoading ? " · обновление" : ""}
            </span>
          ) : undefined
        }
      />

      {canViewCustomerReturns ? (
        <SegmentedControl
          value={section}
          options={inventorySections}
          onChange={setSection}
          label="Раздел складского учёта"
          className="w-full sm:w-auto"
        />
      ) : null}

      {section === "customer_returns" && canViewCustomerReturns ? (
        <Suspense fallback={<SkeletonRows rows={6} />}>
          <CustomerReturnsPanel />
        </Suspense>
      ) : (
        <>
          {data && <InventorySummary total={data.total} summary={data.summary} />}

          <ConfigurableFilterBar
            preferenceKey={filterPreferenceKey}
            filters={[
              {
                id: "batch_number",
                label: "Номер партии",
                content: (
                  <div>
                    <Label htmlFor="batch_number_filter">Номер партии</Label>
                    <Input
                      id="batch_number_filter"
                      value={batchNumberInput}
                      onChange={(event) => setBatchNumberInput(event.target.value)}
                      placeholder="Например, LOT-2408"
                      autoComplete="off"
                      className="w-full sm:w-48"
                    />
                  </div>
                ),
                active: Boolean(batchNumberInput),
                onClear: () => {
                  setBatchNumberInput("");
                  setBatchNumber("");
                  setPage(1);
                },
                alwaysVisible: true,
              },
              {
                id: "product",
                label: "Товар",
                content: (
                  <div className="w-full sm:w-72">
                    <Label htmlFor="batch_catalog_filter">Товар</Label>
                    <CatalogPicker
                      id="batch_catalog_filter"
                      value={catalogId}
                      onChange={(id) => {
                        setCatalogId(id);
                        setPage(1);
                      }}
                      placeholder="Найти товар…"
                      clearable
                    />
                  </div>
                ),
                active: Boolean(catalogId),
                onClear: () => {
                  setCatalogId("");
                  setPage(1);
                },
                defaultVisible: true,
              },
              {
                id: "branch",
                label: "Точка",
                content: (
                  <div>
                    <Label htmlFor="batch_branch_filter">Аптечная точка</Label>
                    <Select
                      id="batch_branch_filter"
                      value={branchId}
                      onChange={(event) => {
                        setBranchId(event.target.value);
                        setPage(1);
                      }}
                      className="w-full sm:w-52"
                    >
                      <option value="">Все аптечные точки</option>
                      {branches.data?.map((branch) => (
                        <option key={branch.id} value={branch.id}>
                          {branch.name}
                        </option>
                      ))}
                    </Select>
                    {branches.error && (
                      <button
                        type="button"
                        className="mt-1 block text-left text-xs text-danger hover:underline"
                        onClick={() => void branches.refetch()}
                      >
                        Не удалось загрузить точки. Повторить
                      </button>
                    )}
                  </div>
                ),
                active: Boolean(branchId),
                activeLabel: branchFilterName ? `Точка: ${branchFilterName}` : undefined,
                onClear: () => {
                  setBranchId("");
                  setPage(1);
                },
                defaultVisible: true,
              },
              {
                id: "expiry",
                label: "Срок годности",
                content: (
                  <div>
                    <Label htmlFor="batch_expiry_filter">Срок годности</Label>
                    <Select
                      id="batch_expiry_filter"
                      value={expiry}
                      onChange={(event) => {
                        setExpiry(event.target.value as ExpiryStatus | "");
                        setPage(1);
                      }}
                      className="w-full sm:w-48"
                    >
                      <option value="">Все зоны</option>
                      {expiryOptions.map((status) => (
                        <option key={status} value={status}>
                          {expiryLabel[status]}
                        </option>
                      ))}
                    </Select>
                  </div>
                ),
                active: Boolean(expiry),
                activeLabel: expiry ? `Срок годности: ${expiryLabel[expiry]}` : undefined,
                onClear: () => {
                  setExpiry("");
                  setPage(1);
                },
                defaultVisible: true,
              },
              {
                id: "blocked",
                label: "Доступность",
                content: (
                  <div>
                    <Label htmlFor="batch_blocked_filter">Доступность</Label>
                    <Select
                      id="batch_blocked_filter"
                      value={blockedFilter}
                      onChange={(event) => {
                        setBlockedFilter(event.target.value as BlockedFilter);
                        setPage(1);
                      }}
                      className="w-full sm:w-48"
                    >
                      <option value="">Все</option>
                      <option value="active">Доступные к продаже</option>
                      <option value="blocked">Заблокированные</option>
                    </Select>
                  </div>
                ),
                active: Boolean(blockedFilter),
                activeLabel: `Доступность: ${blockedFilter === "blocked" ? "Заблокированные" : "Доступные к продаже"}`,
                onClear: () => {
                  setBlockedFilter("");
                  setPage(1);
                },
              },
              {
                id: "empty",
                label: "Пустые партии",
                content: (
                  <div className="flex h-10 items-center">
                    <Switch
                      label="Показывать пустые партии"
                      checked={showEmpty}
                      onChange={(event) => {
                        setShowEmpty(event.target.checked);
                        setPage(1);
                      }}
                    />
                  </div>
                ),
                active: showEmpty,
                activeLabel: "Пустые партии: Показывать",
                onClear: () => {
                  setShowEmpty(false);
                  setPage(1);
                },
              },
            ]}
            onResetValues={resetFilters}
            actions={
              <div className="flex min-h-[var(--control-height-md)] min-w-0 items-center gap-3">
                {isDesktopLayout && <BatchViewControl value={view} onChange={changeView} />}
                <span
                  className="whitespace-nowrap text-sm text-foreground-muted"
                  aria-live="polite"
                >
                  {isShowingPreviousResults ? "Обновляем список…" : `Найдено: ${data?.total ?? 0}`}
                </span>
              </div>
            }
          />

          {error && data && (
            <div
              role="status"
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/40 bg-warning-subtle px-4 py-3 text-sm text-warning-foreground"
            >
              <span>{describeApiError(error, "Не удалось обновить список партий")}</span>
              <Button
                variant="ghost"
                size="sm"
                isLoading={isFetching}
                onClick={() => void refetch()}
              >
                Повторить
              </Button>
            </div>
          )}

          {isLoading ? (
            <div role="status" aria-label="Загрузка партий">
              <span className="sr-only">Загружаем список партий</span>
              <SkeletonRows rows={7} />
            </div>
          ) : error && !data ? (
            <div
              role="alert"
              className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
            >
              <p>{describeApiError(error, "Не удалось загрузить партии")}</p>
              <Button
                variant="secondary"
                size="sm"
                className="mt-3"
                isLoading={isFetching}
                onClick={() => void refetch()}
              >
                Повторить
              </Button>
            </div>
          ) : !data || data.items.length === 0 ? (
            <TableEmpty
              title={filtersActive ? "Партии не найдены" : "На складе пока нет партий"}
              action={
                filtersActive ? (
                  <Button variant="secondary" size="sm" onClick={resetFilters}>
                    Сбросить фильтры
                  </Button>
                ) : undefined
              }
            >
              {filtersActive
                ? "Измените условия поиска или верните стандартный набор фильтров."
                : "Партии появятся после принятия первого прихода."}
            </TableEmpty>
          ) : (
            <>
              {view === "cards" || !isDesktopLayout ? (
                <BatchCards
                  items={data.items}
                  onOpen={openBatch}
                  disabled={isShowingPreviousResults}
                />
              ) : showSplitWorkspace ? (
                <div className="grid min-w-0 grid-cols-[minmax(0,1.65fr)_minmax(23rem,1fr)] items-start gap-4">
                  <BatchTable
                    items={data.items}
                    selectedId={selectedBatch?.id ?? null}
                    onOpen={openBatch}
                    disabled={isShowingPreviousResults}
                  />
                  {selectedBatch && (
                    <section
                      aria-label={`Карточка партии ${selectedBatch.batch_number ?? "без номера"}`}
                      className="sticky top-[calc(var(--app-header-height)+1rem)] max-h-[calc(100vh-var(--app-header-height)-2rem)] min-w-0 overflow-y-auto rounded-lg border border-border bg-surface"
                    >
                      <BatchDetailModal
                        batchId={selectedBatch.id}
                        onClose={() => setSelectedBatchId(null)}
                        mode="preview"
                        onOpenFull={() => setOpenBatchId(selectedBatch.id)}
                      />
                    </section>
                  )}
                </div>
              ) : (
                <BatchTable
                  items={data.items}
                  selectedId={null}
                  onOpen={openBatch}
                  disabled={isShowingPreviousResults}
                />
              )}
              <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
            </>
          )}

          <Modal
            open={openBatchId !== null}
            onClose={() => setOpenBatchId(null)}
            title={
              modalBatch?.batch_number ? `Партия ${modalBatch.batch_number}` : "Карточка партии"
            }
            className="max-w-5xl"
            bodyClassName="p-0 sm:p-0"
          >
            {openBatchId && (
              <BatchDetailModal batchId={openBatchId} onClose={() => setOpenBatchId(null)} />
            )}
          </Modal>
        </>
      )}
    </div>
  );
}

function InventorySummary({
  total,
  summary,
}: {
  total: number;
  summary: {
    total_qty: string;
    purchase_value: string | null;
    sale_value: string;
    attention_count: number;
    expired_count: number;
    blocked_count: number;
  };
}): JSX.Element {
  return (
    <section
      aria-label="Сводка по партиям"
      className="overflow-hidden rounded-lg border border-border bg-surface"
    >
      <div className="grid grid-cols-2 gap-px bg-border md:grid-cols-4">
        <SummaryMetric label="Найдено партий" value={total.toLocaleString("ru-RU")} />
        <SummaryMetric label="Остаток" value={formatInventoryQuantity(summary.total_qty)} />
        <SummaryMetric
          label="Требуют внимания"
          value={summary.attention_count.toLocaleString("ru-RU")}
          detail={
            summary.expired_count > 0 || summary.blocked_count > 0
              ? `просрочено: ${summary.expired_count} · заблокировано: ${summary.blocked_count}`
              : summary.attention_count > 0
                ? `скоро истекают: ${summary.attention_count}`
                : "критичных нет"
          }
          tone={summary.attention_count > 0 ? "danger" : "success"}
        />
        <SummaryMetric
          label={summary.purchase_value === null ? "Розничная стоимость" : "Стоимость остатка"}
          value={formatInventoryMoney(summary.purchase_value ?? summary.sale_value)}
          detail={
            summary.purchase_value === null
              ? "по действующим ценам продажи"
              : `в рознице: ${formatInventoryMoney(summary.sale_value)}`
          }
        />
      </div>
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  detail,
  tone = "default",
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: "default" | "success" | "danger";
}): JSX.Element {
  const valueTone =
    tone === "danger"
      ? "text-danger"
      : tone === "success"
        ? "text-success-foreground"
        : "text-foreground";
  return (
    <div className="min-w-0 bg-surface px-4 py-4 text-center">
      <p className="text-sm text-foreground-muted">{label}</p>
      <p className={`mt-1 truncate text-2xl font-semibold tabular-nums ${valueTone}`}>{value}</p>
      {detail && <p className="mt-0.5 truncate text-xs text-foreground-muted">{detail}</p>}
    </div>
  );
}

function BatchTable({
  items,
  selectedId,
  onOpen,
  disabled = false,
}: {
  items: BatchWithExpiry[];
  selectedId: string | null;
  onOpen: (batch: BatchWithExpiry) => void;
  disabled?: boolean;
}): JSX.Element {
  return (
    <Table className="min-w-full" aria-label="Партии товаров">
      <THead>
        <TR>
          <TH>Товар</TH>
          <TH>Точка</TH>
          <TH>Срок годности</TH>
          <TH className="text-right">Остаток</TH>
          <TH className="text-right">Цена продажи</TH>
          <TH className="w-12 text-right">
            <span className="sr-only">Карточка</span>
          </TH>
        </TR>
      </THead>
      <TBody>
        {items.map((batch) => (
          <TR
            key={batch.id}
            className={cn(
              selectedId === batch.id &&
                "bg-primary/[0.055] shadow-[inset_3px_0_0_hsl(var(--primary))] hover:bg-primary/[0.07]",
            )}
          >
            <TD className="max-w-64">
              <button
                type="button"
                className="max-w-64 truncate text-left font-semibold text-foreground hover:text-primary"
                disabled={disabled}
                onClick={() => onOpen(batch)}
              >
                {batch.catalog_name}
              </button>
              {productSubtitle(batch) && (
                <p className="truncate text-xs text-foreground-muted">{productSubtitle(batch)}</p>
              )}
              <p className="mt-1 font-mono text-xs text-foreground-muted">
                {batch.batch_number ?? "Без номера"}
              </p>
            </TD>
            <TD className="max-w-52">
              <p className="truncate">{batch.branch_name}</p>
              {batch.is_blocked && (
                <Badge tone="danger" className="mt-1">
                  Заблокирована
                </Badge>
              )}
            </TD>
            <TD className="whitespace-nowrap">
              <span>{formatInventoryDate(batch.expires_at)}</span>
              <p className="mt-1 text-xs text-foreground-muted">
                {expiryHint(batch.days_to_expiry)}
              </p>
              <Badge tone={expiryTone[batch.expiry_status]} className="mt-1">
                {expiryLabel[batch.expiry_status]}
              </Badge>
            </TD>
            <TD className="min-w-32 text-right">
              <p className="font-mono font-medium tabular-nums">
                {formatInventoryQuantity(batch.qty_remaining)} из{" "}
                {formatInventoryQuantity(batch.qty_initial)}
              </p>
              <StockBar batch={batch} />
            </TD>
            <TD className="whitespace-nowrap text-right">
              <p className="font-mono font-medium tabular-nums">
                {formatInventoryMoney(batch.sale_price, batch.currency)}
              </p>
            </TD>
            <TD className="text-right">
              <Button
                variant="ghost"
                size="sm"
                className="w-[var(--control-height-sm)] px-0"
                aria-label={`Открыть партию ${batch.batch_number ?? "без номера"} товара ${batch.catalog_name}`}
                disabled={disabled}
                onClick={() => onOpen(batch)}
              >
                <ChevronRightIcon />
              </Button>
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

function BatchCards({
  items,
  onOpen,
  disabled = false,
}: {
  items: BatchWithExpiry[];
  onOpen: (batch: BatchWithExpiry) => void;
  disabled?: boolean;
}): JSX.Element {
  return (
    <div className="space-y-2">
      {items.map((batch) => (
        <article
          key={batch.id}
          aria-label={`${batch.catalog_name}, партия ${batch.batch_number ?? "без номера"}`}
          className="rounded-lg border border-border bg-surface px-3 py-3"
        >
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-foreground">
                {batch.catalog_name}
              </h2>
              {productSubtitle(batch) && (
                <p className="truncate text-xs text-foreground-muted">{productSubtitle(batch)}</p>
              )}
            </div>
            <Badge tone={expiryTone[batch.expiry_status]} className="shrink-0">
              {expiryLabel[batch.expiry_status]}
            </Badge>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
            <CompactField label="Партия" value={batch.batch_number ?? "Без номера"} mono />
            <CompactField label="Точка" value={batch.branch_name} />
            <CompactField
              label="Срок годности"
              value={formatInventoryDate(batch.expires_at)}
              detail={expiryHint(batch.days_to_expiry)}
            />
            <CompactField
              label="Остаток"
              value={formatInventoryQuantity(batch.qty_remaining)}
              detail={`из ${formatInventoryQuantity(batch.qty_initial)}`}
              mono
            />
          </div>

          {batch.is_blocked && (
            <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger-foreground">
              Заблокирована{batch.block_reason ? `: ${batch.block_reason}` : ""}
            </div>
          )}

          <div className="mt-3 flex items-center justify-between gap-3 border-t border-border pt-3">
            <div className="min-w-0">
              <p className="text-xs text-foreground-muted">Цена продажи</p>
              <p className="truncate font-mono text-sm font-semibold tabular-nums">
                {formatInventoryMoney(batch.sale_price, batch.currency)}
              </p>
            </div>
            <Button
              className="min-h-11 shrink-0"
              variant="secondary"
              disabled={disabled}
              aria-label={`Открыть партию ${batch.batch_number ?? "без номера"} товара ${batch.catalog_name}`}
              onClick={() => onOpen(batch)}
            >
              Открыть
            </Button>
          </div>
        </article>
      ))}
    </div>
  );
}

function StockBar({ batch }: { batch: BatchWithExpiry }): JSX.Element {
  const initial = Number(batch.qty_initial);
  const remaining = Number(batch.qty_remaining);
  const percent = initial > 0 ? Math.max(0, Math.min(100, (remaining / initial) * 100)) : 0;
  return (
    <div
      className="mt-1 ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-foreground/10"
      role="progressbar"
      aria-label="Остаток партии"
      aria-valuemin={0}
      aria-valuemax={initial}
      aria-valuenow={remaining}
    >
      <div className="h-full rounded-full bg-primary" style={{ width: `${percent}%` }} />
    </div>
  );
}

function CompactField({
  label,
  value,
  detail,
  mono = false,
}: {
  label: string;
  value: string;
  detail?: string;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className={`truncate ${mono ? "font-mono tabular-nums" : ""}`}>{value}</p>
      {detail && <p className="truncate text-xs text-foreground-muted">{detail}</p>}
    </div>
  );
}

function BatchViewControl({
  value,
  onChange,
}: {
  value: BatchView;
  onChange: (value: BatchView) => void;
}): JSX.Element {
  return (
    <div
      className="inline-flex overflow-hidden rounded-md border border-input bg-surface"
      role="group"
      aria-label="Вид партий"
    >
      <button
        type="button"
        className={cn(
          "grid h-[var(--control-height-md)] w-[var(--control-height-md)] place-items-center transition-colors duration-fast",
          value === "table"
            ? "bg-primary/10 text-primary"
            : "text-foreground-muted hover:bg-foreground/5",
        )}
        aria-label="Показать таблицей"
        aria-pressed={value === "table"}
        onClick={() => onChange("table")}
      >
        <TableViewIcon />
      </button>
      <button
        type="button"
        className={cn(
          "grid h-[var(--control-height-md)] w-[var(--control-height-md)] place-items-center border-l border-input transition-colors duration-fast",
          value === "cards"
            ? "bg-primary/10 text-primary"
            : "text-foreground-muted hover:bg-foreground/5",
        )}
        aria-label="Показать карточками"
        aria-pressed={value === "cards"}
        onClick={() => onChange("cards")}
      >
        <GridViewIcon />
      </button>
    </div>
  );
}

function readBatchView(): BatchView {
  if (typeof window === "undefined") return "table";
  try {
    return window.localStorage.getItem(VIEW_STORAGE_KEY) === "cards" ? "cards" : "table";
  } catch {
    return "table";
  }
}

function writeBatchView(view: BatchView): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, view);
  } catch {
    // The view preference is optional; inventory workflows must remain available.
  }
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? false
      : window.matchMedia(query).matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia(query);
    const onChange = () => setMatches(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

function ChevronRightIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="m7 4 6 6-6 6" />
    </svg>
  );
}

function TableViewIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <rect x="3" y="3.5" width="14" height="13" rx="1" />
      <path d="M3 8h14M7.5 3.5v13" />
    </svg>
  );
}

function GridViewIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <rect x="3" y="3" width="5" height="5" rx="0.5" />
      <rect x="12" y="3" width="5" height="5" rx="0.5" />
      <rect x="3" y="12" width="5" height="5" rx="0.5" />
      <rect x="12" y="12" width="5" height="5" rx="0.5" />
    </svg>
  );
}
