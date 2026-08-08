import { useDeferredValue, useMemo, useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfigurableFilterBar,
  Input,
  Label,
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
import { describeApiError } from "@/lib/errorMessages";

import { billingYear, formatBillingDate, formatBillingMoney } from "./format";
import { InvoiceDetailModal } from "./InvoiceDetailModal";
import {
  billingPeriodLabel,
  invoiceStatusLabel,
  invoiceStatusTone,
  subscriptionStatusLabel,
  subscriptionStatusTone,
} from "./labels";
import { useInvoicesQuery, useSubscriptionQuery } from "./queries";
import { type Invoice, type InvoiceStatus } from "./types";

interface InvoiceSummary {
  outstandingCount: number;
  outstandingTotal: number;
  paidCount: number;
  focusInvoice: Invoice | null;
}

const EMPTY_INVOICES: readonly Invoice[] = [];
const INVOICE_PAGE_SIZE = 10;

export function BillingPage(): JSX.Element {
  const subscription = useSubscriptionQuery();
  const invoices = useInvoicesQuery();
  const filterPreferenceKey = useFilterPreferenceKey("billing");
  const [openInvoiceId, setOpenInvoiceId] = useState<string | null>(null);
  const [invoiceSearch, setInvoiceSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<InvoiceStatus | "">("");
  const [yearFilter, setYearFilter] = useState("");
  const [invoicePage, setInvoicePage] = useState(1);
  const deferredInvoiceSearch = useDeferredValue(invoiceSearch);
  const subscriptionData = subscription.data;
  const invoiceItems = invoices.data ?? EMPTY_INVOICES;

  const invoiceYears = useMemo(
    () =>
      Array.from(new Set(invoiceItems.map((invoice) => billingYear(invoice.issued_at)))).sort(
        (left, right) => right.localeCompare(left),
      ),
    [invoiceItems],
  );

  const filteredInvoices = useMemo(() => {
    const normalizedSearch = deferredInvoiceSearch.trim().toLocaleLowerCase("ru-RU");
    return invoiceItems.filter(
      (invoice) =>
        (!normalizedSearch ||
          invoice.invoice_number.toLocaleLowerCase("ru-RU").includes(normalizedSearch)) &&
        (!statusFilter || invoice.status === statusFilter) &&
        (!yearFilter || billingYear(invoice.issued_at) === yearFilter),
    );
  }, [deferredInvoiceSearch, invoiceItems, statusFilter, yearFilter]);

  const invoiceSummary = useMemo(() => summarizeInvoices(invoiceItems), [invoiceItems]);
  const totalInvoicePages = Math.max(1, Math.ceil(filteredInvoices.length / INVOICE_PAGE_SIZE));
  const visibleInvoicePage = Math.min(invoicePage, totalInvoicePages);
  const visibleInvoices = useMemo(
    () =>
      filteredInvoices.slice(
        (visibleInvoicePage - 1) * INVOICE_PAGE_SIZE,
        visibleInvoicePage * INVOICE_PAGE_SIZE,
      ),
    [filteredInvoices, visibleInvoicePage],
  );
  const hasActiveFilters = Boolean(invoiceSearch || statusFilter || yearFilter);
  const summaryCurrency =
    invoiceSummary.focusInvoice?.currency ?? subscriptionData?.currency ?? "TJS";
  const settlementTone = invoiceSummary.focusInvoice
    ? invoiceSummary.focusInvoice.status === "overdue"
      ? "danger"
      : "warning"
    : "success";
  const settlementLabel = invoiceSummary.focusInvoice
    ? invoiceSummary.focusInvoice.status === "overdue"
      ? "Требует оплаты"
      : "Ожидает оплаты"
    : "Оплачено";

  const resetFilters = () => {
    setInvoiceSearch("");
    setStatusFilter("");
    setYearFilter("");
    setInvoicePage(1);
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="Биллинг"
        description="Подписка, состояние расчётов и история счетов аптеки."
      />

      <Card aria-labelledby="current-subscription-heading">
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle id="current-subscription-heading">Текущая подписка</CardTitle>
          {subscriptionData ? (
            <Badge tone={subscriptionStatusTone[subscriptionData.status]}>
              {subscriptionStatusLabel[subscriptionData.status]}
            </Badge>
          ) : null}
        </CardHeader>
        <CardContent>
          {subscription.isLoading ? (
            <SubscriptionSkeleton />
          ) : subscription.error ? (
            <BillingError
              message={describeApiError(subscription.error, "Не удалось загрузить подписку")}
              retrying={subscription.isFetching}
              onRetry={() => void subscription.refetch()}
            />
          ) : !subscriptionData ? (
            <div className="py-4 text-center sm:text-left">
              <p className="text-base font-semibold text-foreground">Подписка не подключена</p>
              <p className="mt-1 text-sm leading-6 text-foreground-muted">
                Свяжитесь с поддержкой Aurum Pharma, чтобы активировать обслуживание аптеки.
              </p>
            </div>
          ) : (
            <dl className="grid grid-cols-2 gap-x-8 gap-y-5 xl:grid-cols-[minmax(13rem,1.35fr)_repeat(4,minmax(8rem,0.8fr))] xl:items-end">
              <div className="col-span-2 min-w-0 xl:col-span-1">
                <dt className="text-xs font-medium uppercase text-foreground-muted">План</dt>
                <dd className="mt-1 break-words text-xl font-semibold text-foreground">
                  {subscriptionData.plan_name}
                </dd>
                <dd className="mt-0.5 break-all font-mono text-xs text-foreground-muted">
                  {subscriptionData.plan_code}
                </dd>
              </div>
              <BillingField
                label="Период оплаты"
                value={billingPeriodLabel[subscriptionData.billing_period]}
              />
              <BillingField label="Точек" value={subscriptionData.branches_count} />
              <BillingField
                label="Стоимость периода"
                value={formatBillingMoney(subscriptionData.amount, subscriptionData.currency)}
                emphasis
              />
              <BillingField
                label="Действует до"
                value={formatBillingDate(subscriptionData.period_end)}
              />
            </dl>
          )}
        </CardContent>
      </Card>

      <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_21rem] xl:items-start">
        <section
          className="min-w-0 space-y-3"
          aria-labelledby="billing-invoices-heading"
          aria-label="История счетов"
        >
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 id="billing-invoices-heading" className="text-base font-semibold text-foreground">
                История счетов
              </h2>
              <p className="mt-0.5 text-xs text-foreground-muted">
                Откройте счёт, чтобы посмотреть начисления и зарегистрированные платежи.
              </p>
            </div>
            {!invoices.isLoading && !invoices.error ? (
              <Badge tone="neutral" aria-live="polite">
                {invoiceCountLabel(filteredInvoices.length)}
              </Badge>
            ) : null}
          </div>

          <ConfigurableFilterBar
            preferenceKey={filterPreferenceKey}
            filters={[
              {
                id: "number",
                label: "Номер счёта",
                content: (
                  <div>
                    <Label htmlFor="billing-invoice-search">Номер счёта</Label>
                    <Input
                      id="billing-invoice-search"
                      type="search"
                      autoComplete="off"
                      placeholder="Например, AP-2026"
                      value={invoiceSearch}
                      onChange={(event) => {
                        setInvoiceSearch(event.target.value);
                        setInvoicePage(1);
                      }}
                    />
                  </div>
                ),
                active: Boolean(invoiceSearch),
                onClear: () => {
                  setInvoiceSearch("");
                  setInvoicePage(1);
                },
                alwaysVisible: true,
              },
              {
                id: "status",
                label: "Статус",
                content: (
                  <div>
                    <Label htmlFor="billing-invoice-status">Статус</Label>
                    <Select
                      id="billing-invoice-status"
                      value={statusFilter}
                      onChange={(event) => {
                        setStatusFilter(event.target.value as InvoiceStatus | "");
                        setInvoicePage(1);
                      }}
                      className="w-full sm:w-44"
                    >
                      <option value="">Все статусы</option>
                      {(Object.keys(invoiceStatusLabel) as InvoiceStatus[]).map((status) => (
                        <option key={status} value={status}>
                          {invoiceStatusLabel[status]}
                        </option>
                      ))}
                    </Select>
                  </div>
                ),
                active: Boolean(statusFilter),
                onClear: () => {
                  setStatusFilter("");
                  setInvoicePage(1);
                },
                defaultVisible: true,
              },
              {
                id: "year",
                label: "Год",
                content: (
                  <div>
                    <Label htmlFor="billing-invoice-year">Год</Label>
                    <Select
                      id="billing-invoice-year"
                      value={yearFilter}
                      onChange={(event) => {
                        setYearFilter(event.target.value);
                        setInvoicePage(1);
                      }}
                      disabled={invoiceYears.length === 0}
                      className="w-full sm:w-32"
                    >
                      <option value="">Все годы</option>
                      {invoiceYears.map((year) => (
                        <option key={year} value={year}>
                          {year}
                        </option>
                      ))}
                    </Select>
                  </div>
                ),
                active: Boolean(yearFilter),
                onClear: () => {
                  setYearFilter("");
                  setInvoicePage(1);
                },
                defaultVisible: true,
              },
            ]}
            onResetValues={resetFilters}
          />

          {invoices.isLoading ? (
            <div className="rounded-lg border border-border bg-surface p-4" role="status">
              <span className="sr-only">Загрузка счетов…</span>
              <SkeletonRows rows={6} />
            </div>
          ) : invoices.error ? (
            <BillingError
              message={describeApiError(invoices.error, "Не удалось загрузить счета")}
              retrying={invoices.isFetching}
              onRetry={() => void invoices.refetch()}
            />
          ) : filteredInvoices.length === 0 ? (
            <TableEmpty title={hasActiveFilters ? "Счета не найдены" : "Счетов пока нет"}>
              {hasActiveFilters
                ? "Измените или сбросьте фильтры, чтобы увидеть другие счета."
                : "Новые счета появятся здесь после выставления администрацией Aurum Pharma."}
            </TableEmpty>
          ) : (
            <>
              <InvoiceHistory invoices={visibleInvoices} onOpenInvoice={setOpenInvoiceId} />
              {filteredInvoices.length > INVOICE_PAGE_SIZE ? (
                <Pagination
                  page={visibleInvoicePage}
                  pageSize={INVOICE_PAGE_SIZE}
                  total={filteredInvoices.length}
                  onPage={setInvoicePage}
                />
              ) : null}
            </>
          )}
        </section>

        <Card
          className="order-first xl:order-none xl:sticky xl:top-4"
          aria-labelledby="billing-settlement-heading"
        >
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle id="billing-settlement-heading">Состояние расчётов</CardTitle>
            {!invoices.isLoading && !invoices.error ? (
              <Badge tone={settlementTone}>{settlementLabel}</Badge>
            ) : null}
          </CardHeader>
          <CardContent>
            {invoices.isLoading ? (
              <div className="space-y-3" role="status">
                <span className="sr-only">Загрузка состояния расчётов…</span>
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-9 w-44" />
                <Skeleton className="h-20 w-full" />
              </div>
            ) : invoices.error ? (
              <p className="text-sm leading-6 text-foreground-muted">
                Сводка станет доступна после загрузки счетов.
              </p>
            ) : (
              <div className="space-y-5">
                <div aria-live="polite">
                  <p className="text-xs font-medium text-foreground-muted">К оплате</p>
                  <p className="mt-1 font-display text-3xl font-semibold tabular-nums text-foreground">
                    {formatBillingMoney(invoiceSummary.outstandingTotal, summaryCurrency)}
                  </p>
                  <p className="mt-1 text-xs text-foreground-muted">
                    {invoiceSummary.outstandingCount > 0
                      ? openInvoiceCountLabel(invoiceSummary.outstandingCount)
                      : "Открытых счетов нет"}
                  </p>
                </div>

                {invoiceSummary.focusInvoice ? (
                  <div className="border-t border-border pt-4">
                    <p className="text-xs text-foreground-muted">
                      {invoiceSummary.focusInvoice.status === "overdue"
                        ? "Просроченный счёт"
                        : "Ближайший срок оплаты"}
                    </p>
                    <p className="mt-1 break-all font-mono text-sm font-semibold text-foreground">
                      {invoiceSummary.focusInvoice.invoice_number}
                    </p>
                    <p className="mt-1 text-sm text-foreground-secondary">
                      Оплатить до {formatBillingDate(invoiceSummary.focusInvoice.due_at)}
                    </p>
                    <Button
                      variant="secondary"
                      className="mt-3 w-full"
                      onClick={() => setOpenInvoiceId(invoiceSummary.focusInvoice?.id ?? null)}
                    >
                      Открыть счёт
                    </Button>
                  </div>
                ) : (
                  <p className="rounded-md border border-success/25 bg-success-subtle px-3 py-2 text-sm leading-5 text-success-foreground">
                    Все выставленные счета оплачены.
                  </p>
                )}

                <dl className="grid grid-cols-2 gap-x-4 gap-y-4 border-t border-border pt-4">
                  <BillingField label="Всего счетов" value={invoiceItems.length} />
                  <BillingField label="Оплачено" value={invoiceSummary.paidCount} />
                  {subscriptionData ? (
                    <div className="col-span-2">
                      <dt className="text-xs text-foreground-muted">Текущий период</dt>
                      <dd className="mt-1 text-sm font-medium text-foreground">
                        {formatBillingDate(subscriptionData.period_start)} —{" "}
                        {formatBillingDate(subscriptionData.period_end)}
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <InvoiceDetailModal invoiceId={openInvoiceId} onClose={() => setOpenInvoiceId(null)} />
    </div>
  );
}

function InvoiceHistory({
  invoices,
  onOpenInvoice,
}: {
  invoices: readonly Invoice[];
  onOpenInvoice: (invoiceId: string) => void;
}): JSX.Element {
  return (
    <>
      <div className="hidden md:block">
        <Table>
          <THead>
            <TR>
              <TH>Номер</TH>
              <TH>Выставлен</TH>
              <TH>Срок оплаты</TH>
              <TH className="text-right">Сумма</TH>
              <TH>Статус</TH>
              <TH>
                <span className="sr-only">Действие</span>
              </TH>
            </TR>
          </THead>
          <TBody>
            {invoices.map((invoice) => (
              <TR key={invoice.id}>
                <TD className="whitespace-nowrap font-mono text-xs font-semibold text-primary">
                  {invoice.invoice_number}
                </TD>
                <TD className="whitespace-nowrap">{formatBillingDate(invoice.issued_at)}</TD>
                <TD className="whitespace-nowrap">{formatBillingDate(invoice.due_at)}</TD>
                <TD className="whitespace-nowrap text-right tabular-nums">
                  {formatBillingMoney(invoice.amount, invoice.currency)}
                </TD>
                <TD>
                  <Badge tone={invoiceStatusTone[invoice.status]}>
                    {invoiceStatusLabel[invoice.status]}
                  </Badge>
                </TD>
                <TD className="text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-primary"
                    onClick={() => onOpenInvoice(invoice.id)}
                    aria-label={`Открыть счёт ${invoice.invoice_number}`}
                  >
                    Открыть
                  </Button>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </div>

      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface md:hidden">
        {invoices.map((invoice) => (
          <li key={invoice.id}>
            <button
              type="button"
              className="block min-h-24 w-full px-4 py-3 text-left transition-colors duration-fast hover:bg-foreground/[0.025] active:bg-foreground/5"
              onClick={() => onOpenInvoice(invoice.id)}
              aria-label={`Открыть счёт ${invoice.invoice_number}`}
            >
              <span className="flex items-start justify-between gap-3">
                <span className="min-w-0">
                  <span className="block break-all font-mono text-sm font-semibold text-primary">
                    {invoice.invoice_number}
                  </span>
                  <span className="mt-1 block text-xs text-foreground-muted">
                    Срок: {formatBillingDate(invoice.due_at)}
                  </span>
                </span>
                <Badge tone={invoiceStatusTone[invoice.status]}>
                  {invoiceStatusLabel[invoice.status]}
                </Badge>
              </span>
              <span className="mt-3 block text-lg font-semibold tabular-nums text-foreground">
                {formatBillingMoney(invoice.amount, invoice.currency)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

function SubscriptionSkeleton(): JSX.Element {
  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-5" role="status">
      <span className="sr-only">Загрузка подписки…</span>
      {Array.from({ length: 5 }).map((_, index) => (
        <div key={index} className="space-y-2" aria-hidden="true">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-6 w-full max-w-40" />
        </div>
      ))}
    </div>
  );
}

function BillingField({
  label,
  value,
  emphasis = false,
}: {
  label: string;
  value: React.ReactNode;
  emphasis?: boolean;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd
        className={
          emphasis
            ? "mt-1 break-words font-display text-lg font-semibold tabular-nums text-foreground"
            : "mt-1 break-words text-sm font-medium text-foreground"
        }
      >
        {value}
      </dd>
    </div>
  );
}

function BillingError({
  message,
  retrying,
  onRetry,
}: {
  message: string;
  retrying: boolean;
  onRetry: () => void;
}): JSX.Element {
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2"
      role="alert"
    >
      <p className="text-sm text-danger-foreground">{message}</p>
      <Button variant="secondary" size="sm" isLoading={retrying} onClick={onRetry}>
        Повторить
      </Button>
    </div>
  );
}

function summarizeInvoices(invoices: readonly Invoice[]): InvoiceSummary {
  let outstandingCount = 0;
  let outstandingTotal = 0;
  let paidCount = 0;
  let focusInvoice: Invoice | null = null;

  for (const invoice of invoices) {
    if (invoice.status === "paid") paidCount += 1;
    if (invoice.status !== "open" && invoice.status !== "overdue") continue;

    outstandingCount += 1;
    outstandingTotal += Number(invoice.amount);
    if (!focusInvoice || comesBefore(invoice, focusInvoice)) focusInvoice = invoice;
  }

  return { outstandingCount, outstandingTotal, paidCount, focusInvoice };
}

function comesBefore(candidate: Invoice, current: Invoice): boolean {
  if (candidate.status !== current.status) return candidate.status === "overdue";
  return Date.parse(candidate.due_at) < Date.parse(current.due_at);
}

function invoiceCountLabel(count: number): string {
  const absolute = Math.abs(count) % 100;
  const lastDigit = absolute % 10;
  const noun =
    absolute > 10 && absolute < 20
      ? "счетов"
      : lastDigit === 1
        ? "счёт"
        : lastDigit >= 2 && lastDigit <= 4
          ? "счёта"
          : "счетов";
  return `${count} ${noun}`;
}

function openInvoiceCountLabel(count: number): string {
  const absolute = Math.abs(count) % 100;
  const lastDigit = absolute % 10;
  if (absolute > 10 && absolute < 20) return `${count} открытых счетов`;
  if (lastDigit === 1) return `${count} открытый счёт`;
  if (lastDigit >= 2 && lastDigit <= 4) return `${count} открытых счёта`;
  return `${count} открытых счетов`;
}
