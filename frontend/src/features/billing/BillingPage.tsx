import { useDeferredValue, useMemo, useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
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
import { describeApiError } from "@/lib/errorMessages";

import { billingYear, formatBillingDate, formatBillingMoney } from "./format";
import { BillingOverview } from "./BillingOverview";
import { InvoiceDetailModal } from "./InvoiceDetailModal";
import { invoiceStatusLabel, invoiceStatusTone } from "./labels";
import { useInvoicesQuery, useSubscriptionQuery } from "./queries";
import { type Invoice, type InvoiceStatus } from "./types";

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
  const invoicesError = invoices.data === undefined ? invoices.error : null;

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

  const resetFilters = () => {
    setInvoiceSearch("");
    setStatusFilter("");
    setYearFilter("");
    setInvoicePage(1);
  };

  const showInvoiceHistory = () => {
    document.getElementById("billing-invoices")?.scrollIntoView({ block: "start" });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="Тариф и оплата"
        description="Доступ, ближайшие расчёты и история счетов аптеки."
      />

      <BillingOverview
        subscription={subscriptionData}
        invoices={invoiceItems}
        subscriptionLoading={subscription.isLoading}
        subscriptionFetching={subscription.isFetching}
        subscriptionError={subscriptionData === undefined ? subscription.error : null}
        invoicesLoading={invoices.isLoading}
        invoicesFetching={invoices.isFetching}
        invoicesError={invoicesError}
        onRetrySubscription={() => void subscription.refetch()}
        onRetryInvoices={() => void invoices.refetch()}
        onOpenInvoice={setOpenInvoiceId}
        onShowHistory={showInvoiceHistory}
      />

      <section
        id="billing-invoices"
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
          {!invoices.isLoading && !invoicesError ? (
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
        ) : invoicesError ? (
          <BillingError
            message={describeApiError(invoicesError, "Не удалось загрузить счета")}
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
