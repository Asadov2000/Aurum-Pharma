import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";

import {
  Badge,
  Button,
  ConfirmDialog,
  ConfigurableFilterBar,
  Input,
  Label,
  Modal,
  PageHeader,
  Pagination,
  Select,
  Skeleton,
  SkeletonRows,
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
import { hasAnyPermission, hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";
import { SupplierPicker } from "@/features/suppliers/SupplierPicker";
import { cn } from "@/lib/utils";

import { statusLabel, statusOptions, statusTone } from "./labels";
import { NewIncomingForm } from "./NewIncomingForm";
import { useIncomingDocQuery, useIncomingListQuery } from "./queries";
import {
  type IncomingDocument,
  type IncomingDocumentSummary,
  type IncomingDocumentWithItems,
  type IncomingItem,
  type IncomingStatus,
} from "./types";

const PAGE_SIZE = 25;
const VIEW_STORAGE_KEY = "aurum:incoming:view:v1";

type IncomingView = "table" | "cards";

export function IncomingPage(): JSX.Element {
  const { user } = useAuth();
  const navigate = useNavigate();
  const filterPreferenceKey = useFilterPreferenceKey("incoming");
  const canCreate = hasPermission(user, "incoming.create");
  const canDiscoverBranches = hasAnyPermission(user, [
    "branches.view",
    "registers.view",
    "pos.shift_open",
    "pos.shift_close",
    "pos.sell",
    "incoming.view",
    "incoming.create",
  ]);
  const canViewSuppliers = hasPermission(user, "suppliers.view");
  const [branchFilter, setBranchFilter] = useState("");
  const [supplierFilter, setSupplierFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [documentNumberInput, setDocumentNumberInput] = useState("");
  const [documentNumber, setDocumentNumber] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [creatingDirty, setCreatingDirty] = useState(false);
  const [discardCreatingOpen, setDiscardCreatingOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<IncomingView>(readIncomingView);
  const isDesktopLayout = useMediaQuery("(min-width: 768px)");
  const isSplitLayout = useMediaQuery("(min-width: 1280px)");
  const branches = useBranchesQuery(true, canDiscoverBranches);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setDocumentNumber(documentNumberInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timeout);
  }, [documentNumberInput]);

  const params = useMemo(
    () => ({
      branch_id: branchFilter || undefined,
      supplier_id: supplierFilter || undefined,
      status: (statusFilter as IncomingStatus | "") || undefined,
      document_number: documentNumber || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [branchFilter, supplierFilter, statusFilter, documentNumber, dateFrom, dateTo, page],
  );
  const query = useIncomingListQuery(params);
  const isShowingPreviousResults = query.isPlaceholderData && query.isFetching;
  const rows = query.data?.items ?? [];
  const selectedDocument = rows.find((document) => document.id === selectedId) ?? rows[0] ?? null;
  const showSplitWorkspace = isSplitLayout && view === "table";
  const selectedQuery = useIncomingDocQuery(
    showSplitWorkspace ? (selectedDocument?.id ?? null) : null,
  );
  const summary = query.data?.summary ?? summarizeVisibleRows(rows, query.data?.total ?? 0);
  const filtersActive = Boolean(
    branchFilter || supplierFilter || statusFilter || documentNumberInput || dateFrom || dateTo,
  );

  const branchName = (document: IncomingDocument) =>
    document.branch_name ??
    branches.data?.find((branch) => branch.id === document.branch_id)?.name ??
    `Точка ${document.branch_id.slice(0, 8)}`;

  const navigateToDocument = (document: IncomingDocument) => {
    void navigate({ to: "/incoming/$id", params: { id: document.id } });
  };

  const openDocument = (document: IncomingDocument) => {
    if (isShowingPreviousResults) return;
    if (showSplitWorkspace) {
      setSelectedId(document.id);
      return;
    }
    navigateToDocument(document);
  };

  const changeView = (next: IncomingView) => {
    setView(next);
    writeIncomingView(next);
  };

  const resetFilters = () => {
    setBranchFilter("");
    setSupplierFilter("");
    setStatusFilter("");
    setDocumentNumberInput("");
    setDocumentNumber("");
    setDateFrom("");
    setDateTo("");
    setPage(1);
  };

  const closeCreating = () => {
    setCreating(false);
    setCreatingDirty(false);
    setDiscardCreatingOpen(false);
  };

  const requestCloseCreating = () => {
    if (creatingDirty) {
      setDiscardCreatingOpen(true);
      return;
    }
    closeCreating();
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Приёмка товаров"
        description="Проверяйте поставки и принимайте товары на склад без изменения завершённых документов."
        showTitleOnDesktop
        actions={
          canCreate ? (
            <Button size="lg" onClick={() => setCreating(true)}>
              <PlusIcon />
              Новая приёмка
            </Button>
          ) : undefined
        }
      />

      {query.data && <IncomingSummary summary={summary} />}

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "document_number",
            label: "Номер документа",
            content: (
              <div>
                <Label htmlFor="document_number_filter">Номер документа</Label>
                <Input
                  id="document_number_filter"
                  value={documentNumberInput}
                  onChange={(event) => setDocumentNumberInput(event.target.value)}
                  placeholder="Например, ПР-2401"
                  className="w-full sm:w-52"
                />
              </div>
            ),
            active: Boolean(documentNumberInput),
            onClear: () => {
              setDocumentNumberInput("");
              setDocumentNumber("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "branch",
            label: "Аптечная точка",
            content: (
              <div>
                <Label htmlFor="branch_filter">Аптечная точка</Label>
                <Select
                  id="branch_filter"
                  value={branchFilter}
                  onChange={(event) => {
                    setBranchFilter(event.target.value);
                    setPage(1);
                  }}
                  className="w-full sm:w-44"
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
            active: Boolean(branchFilter),
            onClear: () => {
              setBranchFilter("");
              setPage(1);
            },
            defaultVisible: true,
            available: canDiscoverBranches,
          },
          {
            id: "supplier",
            label: "Поставщик",
            content: (
              <div>
                <Label htmlFor="supplier_filter">Поставщик</Label>
                <SupplierPicker
                  id="supplier_filter"
                  value={supplierFilter}
                  onChange={(supplierId) => {
                    setSupplierFilter(supplierId);
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                  placeholder="Все поставщики"
                  clearable
                  includeInactive
                />
              </div>
            ),
            active: Boolean(supplierFilter),
            onClear: () => {
              setSupplierFilter("");
              setPage(1);
            },
            available: canViewSuppliers,
          },
          {
            id: "status",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="status_filter">Статус</Label>
                <Select
                  id="status_filter"
                  value={statusFilter}
                  onChange={(event) => {
                    setStatusFilter(event.target.value);
                    setPage(1);
                  }}
                  className="w-40"
                >
                  <option value="">Все статусы</option>
                  {statusOptions.map((status) => (
                    <option key={status} value={status}>
                      {statusLabel[status]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(statusFilter),
            onClear: () => {
              setStatusFilter("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "period",
            label: "Период",
            content: (
              <div className="grid w-full grid-cols-1 gap-2 sm:w-auto sm:grid-cols-2">
                <div>
                  <Label htmlFor="date_from_filter">С даты</Label>
                  <Input
                    id="date_from_filter"
                    type="date"
                    value={dateFrom}
                    max={dateTo || undefined}
                    onChange={(event) => {
                      setDateFrom(event.target.value);
                      setPage(1);
                    }}
                    className="w-40"
                  />
                </div>
                <div>
                  <Label htmlFor="date_to_filter">По дату</Label>
                  <Input
                    id="date_to_filter"
                    type="date"
                    value={dateTo}
                    min={dateFrom || undefined}
                    onChange={(event) => {
                      setDateTo(event.target.value);
                      setPage(1);
                    }}
                    className="w-40"
                  />
                </div>
              </div>
            ),
            active: Boolean(dateFrom || dateTo),
            onClear: () => {
              setDateFrom("");
              setDateTo("");
              setPage(1);
            },
          },
        ]}
        onResetValues={resetFilters}
        actions={
          <div className="flex min-h-[var(--control-height-md)] min-w-0 items-center gap-3">
            {isDesktopLayout && <IncomingViewControl value={view} onChange={changeView} />}
            <span className="whitespace-nowrap text-sm text-foreground-muted" aria-live="polite">
              {isShowingPreviousResults
                ? "Обновляем список…"
                : `Найдено: ${query.data?.total ?? 0}`}
            </span>
          </div>
        }
      />

      {query.isLoading ? (
        <SkeletonRows rows={6} />
      ) : query.error ? (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
        >
          <p>{describeApiError(query.error, "Не удалось загрузить приходы")}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            isLoading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : !query.data || rows.length === 0 ? (
        <TableEmpty>
          {filtersActive ? "По текущим фильтрам ничего не найдено" : "Приходов пока нет"}
        </TableEmpty>
      ) : (
        <>
          {view === "cards" || !isDesktopLayout ? (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
              {rows.map((document) => (
                <IncomingCard
                  key={document.id}
                  document={document}
                  branchName={branchName(document)}
                  onOpen={openDocument}
                  disabled={isShowingPreviousResults}
                />
              ))}
            </div>
          ) : showSplitWorkspace ? (
            <div className="grid min-w-0 grid-cols-[minmax(0,1.65fr)_minmax(23rem,1fr)] items-start gap-4">
              <IncomingTable
                items={rows}
                selectedId={selectedDocument?.id ?? null}
                branchName={branchName}
                onOpen={openDocument}
                disabled={isShowingPreviousResults}
              />
              {selectedDocument && (
                <IncomingPreviewPanel
                  document={selectedDocument}
                  details={selectedQuery.data}
                  isLoading={selectedQuery.isLoading}
                  error={selectedQuery.error}
                  onRetry={() => void selectedQuery.refetch()}
                  onOpenFull={() => navigateToDocument(selectedDocument)}
                  canEdit={canCreate}
                />
              )}
            </div>
          ) : (
            <IncomingTable
              items={rows}
              selectedId={null}
              branchName={branchName}
              onOpen={openDocument}
              disabled={isShowingPreviousResults}
            />
          )}
          <Pagination page={page} pageSize={PAGE_SIZE} total={query.data.total} onPage={setPage} />
        </>
      )}

      {canCreate && (
        <Modal open={creating} onClose={requestCloseCreating} title="Новая приёмка">
          <NewIncomingForm
            onClose={closeCreating}
            onCancel={requestCloseCreating}
            onDirtyChange={setCreatingDirty}
          />
        </Modal>
      )}
      <ConfirmDialog
        open={discardCreatingOpen}
        title="Закрыть без сохранения?"
        message="Введённые реквизиты новой приёмки будут потеряны."
        confirmLabel="Закрыть без сохранения"
        variant="danger"
        onConfirm={closeCreating}
        onCancel={() => setDiscardCreatingOpen(false)}
      />
    </div>
  );
}

function IncomingSummary({ summary }: { summary: IncomingDocumentSummary }): JSX.Element {
  return (
    <section
      aria-label="Сводка по приходам"
      className="overflow-hidden rounded-lg border border-border bg-surface"
    >
      <dl className="grid grid-cols-2 gap-px bg-border lg:grid-cols-5">
        <SummaryMetric label="Всего документов" value={formatInteger(summary.all_count)} />
        <SummaryMetric
          label="Черновики"
          value={formatInteger(summary.draft_count)}
          tone="warning"
        />
        <SummaryMetric
          label="Приняты"
          value={formatInteger(summary.accepted_count)}
          tone="success"
        />
        <SummaryMetric
          label="Отклонены"
          value={formatInteger(summary.rejected_count)}
          tone="danger"
        />
        <SummaryMetric
          label="Принято на сумму"
          value={formatMoney(summary.accepted_amount, summary.currency)}
          className="col-span-2 lg:col-span-1"
        />
      </dl>
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  tone = "default",
  className,
}: {
  label: string;
  value: string;
  tone?: "default" | "success" | "warning" | "danger";
  className?: string;
}): JSX.Element {
  const toneClass = {
    default: "text-foreground",
    success: "text-success-foreground",
    warning: "text-warning-foreground",
    danger: "text-danger-foreground",
  }[tone];
  return (
    <div className={cn("min-w-0 bg-surface px-4 py-4 text-center", className)}>
      <dt className="text-sm text-foreground-muted">{label}</dt>
      <dd className={cn("mt-1 text-2xl font-semibold tabular-nums", toneClass)}>{value}</dd>
    </div>
  );
}

function IncomingTable({
  items,
  selectedId,
  branchName,
  onOpen,
  disabled = false,
}: {
  items: IncomingDocument[];
  selectedId: string | null;
  branchName: (document: IncomingDocument) => string;
  onOpen: (document: IncomingDocument) => void;
  disabled?: boolean;
}): JSX.Element {
  return (
    <Table className="min-w-full" aria-label="Приходы">
      <THead>
        <TR>
          <TH>Дата и номер</TH>
          <TH>Поставщик</TH>
          <TH>Точка</TH>
          <TH>Статус</TH>
          <TH className="text-right">Сумма</TH>
          <TH className="w-12">
            <span className="sr-only">Открыть</span>
          </TH>
        </TR>
      </THead>
      <TBody>
        {items.map((document) => (
          <TR
            key={document.id}
            className={cn(
              selectedId === document.id &&
                "bg-primary/[0.055] shadow-[inset_3px_0_0_hsl(var(--primary))] hover:bg-primary/[0.07]",
            )}
          >
            <TD>
              <button
                type="button"
                className="text-left disabled:cursor-wait disabled:opacity-60"
                disabled={disabled}
                onClick={() => onOpen(document)}
              >
                <span className="block whitespace-nowrap font-medium text-foreground">
                  {formatDate(document.document_date)}
                </span>
                <span className="mt-0.5 block font-mono text-xs text-foreground-muted">
                  {document.document_number || "Без номера"}
                </span>
              </button>
            </TD>
            <TD className="max-w-56 truncate font-medium">
              {document.supplier_name ?? `Поставщик ${document.supplier_id.slice(0, 8)}`}
            </TD>
            <TD className="max-w-44 truncate">{branchName(document)}</TD>
            <TD>
              <Badge tone={statusTone[document.status]}>{statusLabel[document.status]}</Badge>
            </TD>
            <TD className="whitespace-nowrap text-right tabular-nums">
              {formatMoney(document.total_amount, document.currency)}
            </TD>
            <TD className="text-right">
              <Button
                variant="ghost"
                size="sm"
                className="w-[var(--control-height-sm)] px-0"
                aria-label={`Открыть приход: ${document.document_number || "без номера"}`}
                disabled={disabled}
                onClick={() => onOpen(document)}
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

function IncomingCard({
  document,
  branchName,
  onOpen,
  disabled = false,
}: {
  document: IncomingDocument;
  branchName: string;
  onOpen: (document: IncomingDocument) => void;
  disabled?: boolean;
}): JSX.Element {
  return (
    <article className="min-w-0 rounded-lg border border-border bg-surface p-4">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate font-semibold text-foreground">
            {document.document_number
              ? `Приход № ${document.document_number}`
              : "Без номера поставщика"}
          </h2>
          <p className="mt-1 text-sm text-foreground-muted">{formatDate(document.document_date)}</p>
        </div>
        <Badge tone={statusTone[document.status]}>{statusLabel[document.status]}</Badge>
      </div>
      <dl className="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <CardField
          label="Поставщик"
          value={document.supplier_name ?? `Поставщик ${document.supplier_id.slice(0, 8)}`}
        />
        <CardField label="Точка" value={branchName} />
        <CardField label="Сумма" value={formatMoney(document.total_amount, document.currency)} />
      </dl>
      <Button
        className="mt-4 w-full justify-between"
        variant="secondary"
        disabled={disabled}
        onClick={() => onOpen(document)}
      >
        Открыть документ
        <ArrowRightIcon />
      </Button>
    </article>
  );
}

function CardField({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <>
      <dt className="text-foreground-muted">{label}</dt>
      <dd className="min-w-0 break-words text-right text-foreground">{value}</dd>
    </>
  );
}

function IncomingPreviewPanel({
  document,
  details,
  isLoading,
  error,
  onRetry,
  onOpenFull,
  canEdit,
}: {
  document: IncomingDocument;
  details: IncomingDocumentWithItems | undefined;
  isLoading: boolean;
  error: Error | null;
  onRetry: () => void;
  onOpenFull: () => void;
  canEdit: boolean;
}): JSX.Element {
  const current = details ?? document;
  const items = details?.items ?? [];
  const itemSummary = summarizeItems(items);
  const title = current.document_number
    ? `Приход № ${current.document_number}`
    : "Без номера поставщика";

  return (
    <section
      aria-label={`Карточка прихода: ${current.document_number || "без номера"}`}
      className="sticky top-[calc(var(--app-header-height)+1rem)] min-w-0 overflow-hidden rounded-lg border border-border bg-surface"
    >
      <header className="px-5 py-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-words text-xl font-semibold text-foreground">{title}</h2>
          <Badge tone={statusTone[current.status]}>{statusLabel[current.status]}</Badge>
        </div>
        <p className="mt-1 truncate text-sm text-foreground-muted">
          {current.supplier_name ?? `Поставщик ${current.supplier_id.slice(0, 8)}`} ·{" "}
          {current.branch_name ?? `Точка ${current.branch_id.slice(0, 8)}`}
        </p>
        <p className="mt-1 text-xs text-foreground-muted">{formatDate(current.document_date)}</p>
      </header>

      {isLoading ? (
        <div className="space-y-3 border-t border-border px-5 py-4" aria-label="Загрузка прихода">
          <Skeleton className="h-20" />
          <SkeletonRows rows={4} />
        </div>
      ) : error ? (
        <div className="border-t border-border px-5 py-4" role="alert">
          <p className="text-sm text-danger-foreground">
            {describeApiError(error, "Не удалось загрузить позиции прихода")}
          </p>
          <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
            Повторить
          </Button>
        </div>
      ) : details ? (
        <>
          <dl className="grid grid-cols-2 gap-px border-t border-border bg-border">
            <PreviewMetric label="Позиций" value={formatInteger(items.length)} />
            <PreviewMetric label="Количество" value={formatQuantity(itemSummary.quantity)} />
            <PreviewMetric
              label="Закупка"
              value={formatMoney(details.total_amount, details.currency)}
            />
            <PreviewMetric
              label="Потенциал продаж"
              value={formatMoney(itemSummary.saleTotal, details.currency)}
              note={`Наценка ${formatMoney(itemSummary.margin, details.currency)}`}
            />
          </dl>
          <section aria-labelledby="incoming-preview-items" className="border-t border-border">
            <h3 id="incoming-preview-items" className="px-5 py-3 font-semibold text-foreground">
              Позиции документа
            </h3>
            {items.length > 0 ? (
              <div className="divide-y divide-border border-t border-border">
                {items.slice(0, 5).map((item) => (
                  <IncomingItemPreview key={item.id} item={item} />
                ))}
                {items.length > 5 && (
                  <p className="px-5 py-3 text-xs text-primary">Ещё {items.length - 5} позиций</p>
                )}
              </div>
            ) : (
              <p className="border-t border-border px-5 py-4 text-sm text-foreground-muted">
                В документе пока нет позиций.
              </p>
            )}
          </section>
        </>
      ) : null}

      <footer className="border-t border-border px-5 py-4">
        <p className="mb-3 text-xs text-foreground-muted">
          {current.status === "draft"
            ? "Черновик не изменяет складские остатки."
            : current.status === "accepted"
              ? "Позиции документа уже оприходованы на склад."
              : "Отклонённый документ не изменил складские остатки."}
        </p>
        <Button
          className="w-full justify-between"
          variant={canEdit && current.status === "draft" ? "primary" : "secondary"}
          onClick={onOpenFull}
        >
          {canEdit && current.status === "draft" ? "Проверить и принять" : "Открыть документ"}
          <ArrowRightIcon />
        </Button>
      </footer>
    </section>
  );
}

function PreviewMetric({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}): JSX.Element {
  return (
    <div className="min-w-0 bg-surface px-4 py-3 text-center">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className="mt-1 truncate font-semibold tabular-nums text-foreground">{value}</dd>
      {note && <p className="mt-0.5 truncate text-xs text-foreground-muted">{note}</p>}
    </div>
  );
}

function IncomingItemPreview({ item }: { item: IncomingItem }): JSX.Element {
  const amount = Number(item.qty) * Number(item.purchase_price);
  const subtitle = [item.catalog_form, item.catalog_dosage, item.catalog_pack_size]
    .filter(Boolean)
    .join(" · ");
  return (
    <article className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 px-5 py-3 text-xs">
      <div className="min-w-0">
        <p className="truncate font-medium text-foreground">
          {item.catalog_name ?? `Товар ${item.catalog_id.slice(0, 8)}`}
        </p>
        <p className="mt-1 truncate text-foreground-muted">
          {subtitle || "Без характеристик"} · партия {item.batch_number || "без номера"}
        </p>
        <p className="mt-1 text-foreground-muted">Годен до {formatDate(item.expires_at)}</p>
      </div>
      <div className="text-right tabular-nums">
        <p className="font-medium text-foreground">{formatQuantity(Number(item.qty))} ед.</p>
        <p className="mt-1 text-foreground-muted">{formatMoney(amount, item.currency)}</p>
      </div>
    </article>
  );
}

function IncomingViewControl({
  value,
  onChange,
}: {
  value: IncomingView;
  onChange: (value: IncomingView) => void;
}): JSX.Element {
  return (
    <div
      className="inline-flex overflow-hidden rounded-md border border-input bg-surface"
      role="group"
      aria-label="Вид приходов"
    >
      <ViewButton
        active={value === "table"}
        label="Показать таблицей"
        onClick={() => onChange("table")}
      >
        <TableViewIcon />
      </ViewButton>
      <ViewButton
        active={value === "cards"}
        label="Показать карточками"
        onClick={() => onChange("cards")}
        separated
      >
        <GridViewIcon />
      </ViewButton>
    </div>
  );
}

function ViewButton({
  active,
  label,
  onClick,
  separated = false,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  separated?: boolean;
  children: ReactNode;
}): JSX.Element {
  return (
    <button
      type="button"
      className={cn(
        "grid h-[var(--control-height-md)] w-[var(--control-height-md)] place-items-center transition-colors duration-fast",
        separated && "border-l border-input",
        active ? "bg-primary/10 text-primary" : "text-foreground-muted hover:bg-foreground/5",
      )}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function summarizeVisibleRows(rows: IncomingDocument[], total: number): IncomingDocumentSummary {
  return {
    all_count: total,
    draft_count: rows.filter((document) => document.status === "draft").length,
    accepted_count: rows.filter((document) => document.status === "accepted").length,
    rejected_count: rows.filter((document) => document.status === "rejected").length,
    accepted_amount: rows
      .filter((document) => document.status === "accepted")
      .reduce((sum, document) => sum + Number(document.total_amount), 0)
      .toFixed(2),
    currency: rows[0]?.currency ?? "TJS",
  };
}

function summarizeItems(items: IncomingItem[]): {
  quantity: number;
  saleTotal: number;
  margin: number;
} {
  return items.reduce(
    (summary, item) => {
      const qty = Number(item.qty);
      const purchase = qty * Number(item.purchase_price);
      const sale = qty * Number(item.sale_price);
      summary.quantity += qty;
      summary.saleTotal += sale;
      summary.margin += sale - purchase;
      return summary;
    },
    { quantity: 0, saleTotal: 0, margin: 0 },
  );
}

function formatDate(value: string): string {
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return value;
  return new Intl.DateTimeFormat("ru-RU").format(new Date(Date.UTC(year, month - 1, day)));
}

function formatMoney(value: string | number, currency = "TJS"): string {
  const number = Number(value);
  const formatted = Number.isFinite(number)
    ? number.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "0,00";
  return `${formatted} ${currency}`;
}

function formatInteger(value: number): string {
  return value.toLocaleString("ru-RU");
}

function formatQuantity(value: number): string {
  return value.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}

function readIncomingView(): IncomingView {
  if (typeof window === "undefined") return "table";
  try {
    return window.localStorage.getItem(VIEW_STORAGE_KEY) === "cards" ? "cards" : "table";
  } catch {
    return "table";
  }
}

function writeIncomingView(view: IncomingView): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, view);
  } catch {
    // The preference is optional; document workflows must remain available.
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

function PlusIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="M10 3v14M3 10h14" />
    </svg>
  );
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

function ArrowRightIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="M4 10h12M11 5l5 5-5 5" />
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
