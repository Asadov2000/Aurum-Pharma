import { type ReactNode, useEffect, useMemo, useState } from "react";

import {
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
import { describeApiError } from "@/features/foundation/errors";
import { cn } from "@/lib/utils";

import {
  formatSupplierDateTime,
  formatSupplierMoney,
  formatSupplierQuantity,
  supplierProductSubtitle,
} from "./formatters";
import { supplierReturnReasonLabel } from "./labels";
import { useSupplierReturnsQuery, useSupplierSearchQuery } from "./queries";
import { SupplierDetailModal } from "./SupplierDetailModal";
import { SupplierForm } from "./SupplierForm";
import { SupplierReturnForm } from "./SupplierReturnForm";
import { type Supplier, type SupplierReturnDetails, type SupplierSearchSummary } from "./types";

const PAGE_SIZE = 25;

type StatusFilter = "active" | "inactive" | "all";
type SupplierView = "table" | "cards";

const VIEW_STORAGE_KEY = "aurum:suppliers:view:v1";

function supplierStatusLabel(supplier: Supplier): string {
  return supplier.is_active ? "Доступен для приходов" : "Отключён для новых документов";
}

export function SuppliersPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("suppliers");
  const canCreate = hasPermission(user, "suppliers.create");
  const canUpdate = hasPermission(user, "suppliers.update");
  const canCreateReturn = hasPermission(user, "incoming.return");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<Supplier | null>(null);
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [returning, setReturning] = useState<Supplier | null>(null);
  const [creating, setCreating] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [returnDirty, setReturnDirty] = useState(false);
  const [discardTarget, setDiscardTarget] = useState<"editor" | "return" | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<SupplierView>(readSupplierView);
  const isDesktopLayout = useMediaQuery("(min-width: 768px)");
  const isSplitLayout = useMediaQuery("(min-width: 1280px)");

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const params = useMemo(
    () => ({
      q: q || undefined,
      is_active: status === "all" ? undefined : status === "active",
      page,
      page_size: PAGE_SIZE,
    }),
    [page, q, status],
  );
  const query = useSupplierSearchQuery(params);
  const rows = query.data?.items ?? [];
  const isShowingPreviousResults = query.isPlaceholderData && query.isFetching;
  const selectedSupplier = rows.find((supplier) => supplier.id === selectedId) ?? rows[0] ?? null;
  const hasFilters = Boolean(qInput.trim() || status !== "active");

  const resetFilters = () => {
    setQInput("");
    setQ("");
    setStatus("active");
    setPage(1);
  };

  const changeView = (next: SupplierView) => {
    setView(next);
    writeSupplierView(next);
  };

  const openSupplier = (supplier: Supplier) => {
    if (isSplitLayout && view === "table") {
      setSelectedId(supplier.id);
      return;
    }
    setDetail(supplier);
  };

  const closeEditor = () => {
    setCreating(false);
    setEditing(null);
    setEditorDirty(false);
    setDiscardTarget(null);
  };
  const requestEditorClose = () => {
    if (editorDirty) setDiscardTarget("editor");
    else closeEditor();
  };
  const closeReturn = () => {
    setReturning(null);
    setReturnDirty(false);
    setDiscardTarget(null);
  };
  const requestReturnClose = () => {
    if (returnDirty) setDiscardTarget("return");
    else closeReturn();
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Поставщики"
        description="Контакты, реквизиты и возвраты по каждой компании-партнёру."
        showTitleOnDesktop
        meta={
          query.data ? (
            <span aria-live="polite">
              {query.data.total} найдено
              {isShowingPreviousResults ? " · поиск" : query.isFetching ? " · обновление" : ""}
            </span>
          ) : undefined
        }
        actions={
          <>
            {canCreateReturn && isSplitLayout && view === "table" && (
              <Button
                variant="secondary"
                size="lg"
                disabled={isShowingPreviousResults || !selectedSupplier?.is_active}
                onClick={() => selectedSupplier && setReturning(selectedSupplier)}
              >
                <ReturnIcon />
                Оформить возврат
              </Button>
            )}
            {canCreate && (
              <Button size="lg" onClick={() => setCreating(true)}>
                <PlusIcon />
                Добавить поставщика
              </Button>
            )}
          </>
        }
      />

      {query.data && <SupplierSummary summary={query.data.summary} />}

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div className="w-full sm:w-80">
                <Label htmlFor="supplier_search">Поиск</Label>
                <Input
                  id="supplier_search"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Название, контакт, телефон или ИНН"
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
            id: "status",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="supplier_status_filter">Статус</Label>
                <Select
                  id="supplier_status_filter"
                  value={status}
                  onChange={(event) => {
                    setStatus(event.target.value as StatusFilter);
                    setPage(1);
                  }}
                  className="w-full sm:w-44"
                >
                  <option value="active">Доступны для новых приходов</option>
                  <option value="inactive">Не используются в новых документах</option>
                  <option value="all">Все поставщики</option>
                </Select>
              </div>
            ),
            active: status !== "active",
            onClear: () => {
              setStatus("active");
              setPage(1);
            },
            defaultVisible: true,
          },
        ]}
        onResetValues={resetFilters}
        actions={
          <div className="flex min-h-[var(--control-height-md)] min-w-0 items-center gap-3">
            {isDesktopLayout && <SupplierViewControl value={view} onChange={changeView} />}
            <span className="whitespace-nowrap text-sm text-foreground-muted" aria-live="polite">
              Найдено: {query.data?.total ?? 0}
            </span>
          </div>
        }
      />

      {isShowingPreviousResults ? (
        <div
          className="rounded-lg border border-info/30 bg-info-subtle px-3 py-2 text-sm text-info-foreground"
          role="status"
        >
          Ищем поставщиков. Пока показан предыдущий список; действия временно недоступны.
        </div>
      ) : null}

      {query.isLoading ? (
        <SkeletonRows rows={7} />
      ) : query.error && !query.data ? (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
        >
          <p>{describeApiError(query.error, "Не удалось загрузить поставщиков")}</p>
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
      ) : rows.length === 0 ? (
        <TableEmpty
          title={hasFilters ? "Поставщики не найдены" : "Поставщиков пока нет"}
          action={
            hasFilters ? (
              <Button variant="secondary" size="sm" onClick={resetFilters}>
                Сбросить фильтры
              </Button>
            ) : canCreate ? (
              <Button size="sm" onClick={() => setCreating(true)}>
                Добавить поставщика
              </Button>
            ) : undefined
          }
        >
          {hasFilters
            ? "Измените запрос или верните стандартный набор фильтров."
            : "Добавьте первую компанию, чтобы оформлять приходы и возвраты."}
        </TableEmpty>
      ) : (
        <>
          {query.error ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2">
              <p className="text-sm text-foreground-secondary" role="status">
                Показаны ранее загруженные данные. Обновление не удалось.
              </p>
              <Button variant="secondary" size="sm" onClick={() => void query.refetch()}>
                Повторить
              </Button>
            </div>
          ) : null}
          {view === "cards" || !isDesktopLayout ? (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
              {rows.map((supplier) => (
                <SupplierCard
                  key={supplier.id}
                  supplier={supplier}
                  disabled={isShowingPreviousResults}
                  onOpen={openSupplier}
                />
              ))}
            </div>
          ) : isSplitLayout ? (
            <div className="grid min-w-0 grid-cols-[minmax(0,1.75fr)_minmax(21rem,1fr)] items-start gap-4">
              <SupplierTable
                items={rows}
                selectedId={selectedSupplier?.id ?? null}
                disabled={isShowingPreviousResults}
                onOpen={openSupplier}
              />
              {selectedSupplier && (
                <SupplierPreviewPanel
                  supplier={selectedSupplier}
                  disabled={isShowingPreviousResults}
                  onEdit={(supplier) => setEditing(supplier)}
                  onReturn={(supplier) => setReturning(supplier)}
                  onOpenFull={(supplier) => setDetail(supplier)}
                />
              )}
            </div>
          ) : (
            <SupplierTable
              items={rows}
              selectedId={null}
              disabled={isShowingPreviousResults}
              onOpen={openSupplier}
            />
          )}
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={query.data?.total ?? 0}
            onPage={setPage}
          />
        </>
      )}

      <Modal
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={detail?.name ?? "Карточка поставщика"}
        className="max-w-6xl"
        bodyClassName="p-0 sm:p-0"
      >
        {detail && (
          <SupplierDetailModal
            supplier={detail}
            onClose={() => setDetail(null)}
            onEdit={(supplier) => {
              setDetail(null);
              setEditing(supplier);
            }}
          />
        )}
      </Modal>

      {canCreateReturn && (
        <Modal
          open={returning !== null}
          onClose={requestReturnClose}
          title={returning ? `Возврат поставщику: ${returning.name}` : "Возврат поставщику"}
          className="max-w-3xl"
        >
          {returning && (
            <SupplierReturnForm
              supplier={returning}
              onClose={requestReturnClose}
              onDirtyChange={setReturnDirty}
            />
          )}
        </Modal>
      )}

      {(canCreate || canUpdate) && (
        <Modal
          open={creating || editing !== null}
          onClose={requestEditorClose}
          title={editing ? `Изменить поставщика: ${editing.name}` : "Добавить поставщика"}
          className="max-w-2xl"
        >
          <SupplierForm
            supplier={editing}
            onClose={closeEditor}
            onCancel={requestEditorClose}
            onDirtyChange={setEditorDirty}
          />
        </Modal>
      )}

      <ConfirmDialog
        open={discardTarget !== null}
        title="Закрыть без сохранения?"
        message={
          discardTarget === "return"
            ? "Введённые данные возврата не сохранятся."
            : "Изменения в карточке поставщика не сохранятся."
        }
        cancelLabel="Продолжить"
        confirmLabel="Закрыть без сохранения"
        variant="danger"
        onCancel={() => setDiscardTarget(null)}
        onConfirm={discardTarget === "return" ? closeReturn : closeEditor}
      />
    </div>
  );
}

function readSupplierView(): SupplierView {
  if (typeof window === "undefined") return "table";
  try {
    return window.localStorage.getItem(VIEW_STORAGE_KEY) === "cards" ? "cards" : "table";
  } catch {
    return "table";
  }
}

function writeSupplierView(view: SupplierView): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, view);
  } catch {
    // View preference is optional; the supplier workflow must stay available.
  }
}

function SupplierViewControl({
  value,
  onChange,
}: {
  value: SupplierView;
  onChange: (value: SupplierView) => void;
}): JSX.Element {
  return (
    <div
      className="inline-flex overflow-hidden rounded-md border border-input bg-surface"
      role="group"
      aria-label="Вид поставщиков"
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

function SupplierSummary({ summary }: { summary: SupplierSearchSummary }): JSX.Element {
  const contactCoverage = summary.all_count
    ? Math.round((summary.with_contact_count / summary.all_count) * 100)
    : 0;
  return (
    <section
      aria-label="Сводка по поставщикам"
      className="grid grid-cols-2 overflow-hidden rounded-lg border border-border bg-surface md:grid-cols-4"
    >
      <SummaryMetric label="Всего" value={summary.all_count} />
      <SummaryMetric label="Доступны для приходов" value={summary.active_count} tone="success" />
      <SummaryMetric
        label="Отключены"
        value={summary.inactive_count}
        tone={summary.inactive_count > 0 ? "muted" : "default"}
      />
      <SummaryMetric
        label="Есть телефон или email"
        value={summary.with_contact_count}
        detail={`${contactCoverage}% справочника`}
      />
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
  value: number;
  detail?: string;
  tone?: "default" | "success" | "muted";
}): JSX.Element {
  const toneClass =
    tone === "success"
      ? "text-success-foreground"
      : tone === "muted"
        ? "text-foreground-secondary"
        : "text-foreground";
  return (
    <div className="min-w-0 border-b border-r border-border px-4 py-4 text-center last:border-r-0 md:border-b-0">
      <p className="text-sm text-foreground-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>
        {value.toLocaleString("ru-RU")}
      </p>
      {detail && <p className="mt-0.5 truncate text-xs text-foreground-muted">{detail}</p>}
    </div>
  );
}

function SupplierTable({
  items,
  selectedId,
  disabled,
  onOpen,
}: {
  items: Supplier[];
  selectedId: string | null;
  disabled: boolean;
  onOpen: (supplier: Supplier) => void;
}): JSX.Element {
  return (
    <Table className="min-w-full" aria-label="Поставщики">
      <THead>
        <TR>
          <TH>Поставщик</TH>
          <TH>Реквизиты</TH>
          <TH>Контакт</TH>
          <TH>Связь</TH>
          <TH>Статус</TH>
          <TH className="w-12 text-right">
            <span className="sr-only">Карточка</span>
          </TH>
        </TR>
      </THead>
      <TBody>
        {items.map((supplier) => (
          <TR
            key={supplier.id}
            className={cn(
              selectedId === supplier.id &&
                "bg-primary/[0.055] shadow-[inset_3px_0_0_hsl(var(--primary))] hover:bg-primary/[0.07]",
            )}
          >
            <TD>
              <button
                type="button"
                className="max-w-72 text-left font-semibold text-foreground hover:text-primary"
                disabled={disabled}
                onClick={() => onOpen(supplier)}
              >
                {supplier.name}
              </button>
              {supplier.legal_name && (
                <p className="mt-0.5 max-w-72 truncate text-xs text-foreground-muted">
                  {supplier.legal_name}
                </p>
              )}
            </TD>
            <TD>
              <p className="font-mono text-xs tabular-nums">
                {supplier.inn_or_tin || "ИНН не указан"}
              </p>
              {supplier.address && (
                <p className="mt-1 max-w-56 truncate text-xs text-foreground-muted">
                  {supplier.address}
                </p>
              )}
            </TD>
            <TD>{supplier.contact_person || "—"}</TD>
            <TD>
              {supplier.phone ? (
                <a className="hover:text-primary hover:underline" href={`tel:${supplier.phone}`}>
                  {supplier.phone}
                </a>
              ) : (
                <p>—</p>
              )}
              {supplier.email && (
                <a
                  className="mt-0.5 block max-w-52 truncate text-xs text-foreground-muted hover:text-primary hover:underline"
                  href={`mailto:${supplier.email}`}
                  title={supplier.email}
                >
                  {supplier.email}
                </a>
              )}
            </TD>
            <TD>
              <Badge tone={supplier.is_active ? "success" : "neutral"}>
                {supplierStatusLabel(supplier)}
              </Badge>
            </TD>
            <TD className="text-right">
              <Button
                variant="ghost"
                size="sm"
                className="w-[var(--control-height-sm)] px-0"
                aria-label={`Открыть карточку: ${supplier.name}`}
                disabled={disabled}
                onClick={() => onOpen(supplier)}
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

function SupplierPreviewPanel({
  supplier,
  disabled,
  onEdit,
  onReturn,
  onOpenFull,
}: {
  supplier: Supplier;
  disabled: boolean;
  onEdit: (supplier: Supplier) => void;
  onReturn: (supplier: Supplier) => void;
  onOpenFull: (supplier: Supplier) => void;
}): JSX.Element {
  const { user } = useAuth();
  const canUpdate = hasPermission(user, "suppliers.update");
  const canViewReturns = hasPermission(user, "incoming.view");
  const canCreateReturn = hasPermission(user, "incoming.return") && supplier.is_active && !disabled;
  const returns = useSupplierReturnsQuery(
    { supplier_id: supplier.id, page: 1, page_size: 3 },
    canViewReturns,
  );

  return (
    <section
      aria-label={`Карточка поставщика: ${supplier.name}`}
      className="sticky top-[calc(var(--app-header-height)+1rem)] min-w-0 overflow-hidden rounded-lg border border-border bg-surface"
    >
      <header className="px-5 py-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-words text-xl font-semibold text-foreground">
            {supplier.name}
          </h2>
          <Badge tone={supplier.is_active ? "success" : "neutral"}>
            {supplierStatusLabel(supplier)}
          </Badge>
        </div>
        <p className="mt-1 text-sm text-foreground-muted">
          {supplier.legal_name || "Юридическое наименование не указано"}
        </p>

        {(canUpdate || canCreateReturn) && (
          <div
            className={cn(
              "mt-4 grid gap-2",
              canUpdate && canCreateReturn ? "grid-cols-2" : "grid-cols-1",
            )}
          >
            {canUpdate && (
              <Button variant="secondary" disabled={disabled} onClick={() => onEdit(supplier)}>
                <EditIcon />
                Изменить
              </Button>
            )}
            {canCreateReturn && (
              <Button onClick={() => onReturn(supplier)}>
                <ReturnIcon />
                Оформить возврат
              </Button>
            )}
          </div>
        )}
      </header>

      <section
        aria-label="Контакты выбранного поставщика"
        className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 border-t border-border px-5 py-4 text-sm"
      >
        <PreviewField label="Контактное лицо" value={supplier.contact_person || "Не указано"} />
        <PreviewField
          label="Телефон"
          value={
            supplier.phone ? (
              <a className="hover:text-primary hover:underline" href={`tel:${supplier.phone}`}>
                {supplier.phone}
              </a>
            ) : (
              "Не указан"
            )
          }
        />
        <PreviewField
          label="Email"
          value={
            supplier.email ? (
              <a className="hover:text-primary hover:underline" href={`mailto:${supplier.email}`}>
                {supplier.email}
              </a>
            ) : (
              "Не указан"
            )
          }
        />
        <PreviewField label="ИНН поставщика" value={supplier.inn_or_tin || "Не указан"} mono />
        <PreviewField label="Адрес" value={supplier.address || "Не указан"} />
      </section>

      {canViewReturns && (
        <section aria-labelledby="supplier-preview-returns" className="border-t border-border">
          <div className="px-5 py-4">
            <h3 id="supplier-preview-returns" className="font-semibold text-foreground">
              Возвраты поставщику
            </h3>
            {returns.data && (
              <p className="mt-1 text-xs text-foreground-muted">
                {returns.data.total} операций ·{" "}
                {formatSupplierQuantity(returns.data.summary.total_qty)} ед. ·{" "}
                {formatSupplierMoney(returns.data.summary.total_amount)}
              </p>
            )}
          </div>

          {returns.isLoading ? (
            <div className="px-5 pb-4">
              <SkeletonRows rows={3} />
            </div>
          ) : returns.error ? (
            <div className="px-5 pb-4">
              <p className="text-sm text-danger-foreground" role="alert">
                {describeApiError(returns.error, "Не удалось загрузить возвраты")}
              </p>
              <Button
                variant="secondary"
                size="sm"
                className="mt-3"
                onClick={() => void returns.refetch()}
              >
                Повторить
              </Button>
            </div>
          ) : returns.data?.items.length ? (
            <div className="divide-y divide-border border-t border-border">
              {returns.data.items.map((item) => (
                <SupplierReturnPreview key={item.id} item={item} />
              ))}
            </div>
          ) : (
            <p className="border-t border-border px-5 py-4 text-sm text-foreground-muted">
              Возвратов этому поставщику пока не оформляли.
            </p>
          )}
        </section>
      )}

      <div className="border-t border-border px-5 py-3">
        <Button
          className="w-full justify-between"
          variant="ghost"
          disabled={disabled}
          onClick={() => onOpenFull(supplier)}
        >
          Открыть полную карточку
          <ArrowRightIcon />
        </Button>
      </div>
    </section>
  );
}

function PreviewField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}): JSX.Element {
  return (
    <>
      <span className="text-foreground-muted">{label}</span>
      <span className={cn("min-w-0 break-words text-foreground", mono && "tabular-nums")}>
        {value}
      </span>
    </>
  );
}

function SupplierReturnPreview({ item }: { item: SupplierReturnDetails }): JSX.Element {
  return (
    <article className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 px-5 py-3 text-xs">
      <div className="min-w-0">
        <p className="truncate font-medium text-foreground">{item.catalog_name}</p>
        <p className="mt-1 truncate text-foreground-muted">
          {formatSupplierDateTime(item.created_at, item.report_timezone)} ·{" "}
          {supplierReturnReasonLabel[item.reason]}
        </p>
        <p className="mt-1 truncate text-foreground-muted">
          {supplierProductSubtitle(item) || "Без характеристик"} · партия{" "}
          {item.batch_number ?? "без номера"}
        </p>
      </div>
      <div className="text-right tabular-nums">
        <p className="font-medium text-foreground">{formatSupplierQuantity(item.qty)} ед.</p>
        <p className="mt-1 text-foreground-muted">
          {formatSupplierMoney(item.amount, item.currency)}
        </p>
      </div>
    </article>
  );
}

function SupplierCard({
  supplier,
  disabled,
  onOpen,
}: {
  supplier: Supplier;
  disabled: boolean;
  onOpen: (supplier: Supplier) => void;
}): JSX.Element {
  return (
    <article className="rounded-lg border border-border bg-surface px-4 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="break-words text-base font-semibold text-foreground">{supplier.name}</h2>
          <p className="mt-1 truncate text-sm text-foreground-muted">
            {supplier.contact_person || supplier.legal_name || "Контакт не указан"}
          </p>
        </div>
        <Badge tone={supplier.is_active ? "success" : "neutral"}>
          {supplierStatusLabel(supplier)}
        </Badge>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <CardField
          label="Телефон"
          value={
            supplier.phone ? (
              <a className="hover:text-primary hover:underline" href={`tel:${supplier.phone}`}>
                {supplier.phone}
              </a>
            ) : (
              "—"
            )
          }
        />
        <CardField
          label="Email"
          value={
            supplier.email ? (
              <a className="hover:text-primary hover:underline" href={`mailto:${supplier.email}`}>
                {supplier.email}
              </a>
            ) : (
              "—"
            )
          }
        />
        <CardField label="ИНН поставщика" value={supplier.inn_or_tin || "—"} mono />
        <CardField label="Адрес" value={supplier.address || "—"} />
      </div>
      <Button
        className="mt-4 min-h-11 w-full"
        variant="secondary"
        disabled={disabled}
        onClick={() => onOpen(supplier)}
      >
        Открыть карточку
      </Button>
    </article>
  );
}

function CardField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className={`mt-0.5 truncate ${mono ? "font-mono tabular-nums" : ""}`}>{value}</p>
    </div>
  );
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

function ReturnIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      aria-hidden="true"
    >
      <path d="M5 6H2V3" />
      <path d="M2.5 6a8 8 0 1 1-.2 7" />
    </svg>
  );
}

function EditIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      aria-hidden="true"
    >
      <path d="m13.5 3.5 3 3L7 16H4v-3l9.5-9.5Z" />
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

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? false
      : window.matchMedia(query).matches,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);

  return matches;
}
