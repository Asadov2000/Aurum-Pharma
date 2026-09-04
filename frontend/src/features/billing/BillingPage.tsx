import { useDeferredValue, useMemo, useState } from "react";

import {
  Badge,
  Button,
  Card,
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
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/lib/errorMessages";

import { BillingOverview } from "./BillingOverview";
import {
  billingYear,
  formatBillingDate,
  formatBillingDateTime,
  formatBillingMoney,
} from "./format";
import { InvoiceDetailModal } from "./InvoiceDetailModal";
import {
  financialInvoiceStatus,
  financialInvoiceStatusLabel,
  financialInvoiceStatusTone,
} from "./labels";
import { PaymentSubmissionModal } from "./PaymentSubmissionModal";
import { PaymentSubmissionsPanel } from "./PaymentSubmissionsPanel";
import { useFinancialAccountQuery } from "./queries";
import {
  type FinancialInvoiceDisplayStatus,
  type TenantBillingPayment,
  type TenantFinancialInvoice,
} from "./types";
import { useConnectivity } from "@/lib/connectivityContext";

const PAGE_SIZE = 10;
const EMPTY_INVOICES: readonly TenantFinancialInvoice[] = [];
const EMPTY_PAYMENTS: readonly TenantBillingPayment[] = [];
const FILTER_STATUSES: readonly FinancialInvoiceDisplayStatus[] = [
  "overdue",
  "unpaid",
  "partially_paid",
  "paid",
  "written_off",
  "void",
];

export function BillingPage(): JSX.Element {
  const { user } = useAuth();
  const canCreatePaymentSubmission = hasPermission(user, "billing.payment_submission.create");
  const canWithdrawPaymentSubmission = hasPermission(user, "billing.payment_submission.withdraw");
  const online = useConnectivity().canUseServer;
  const accountQuery = useFinancialAccountQuery();
  const account = accountQuery.data;
  const filterPreferenceKey = useFilterPreferenceKey("billing");
  const [openInvoiceId, setOpenInvoiceId] = useState<string | null>(null);
  const [invoiceSearch, setInvoiceSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<FinancialInvoiceDisplayStatus | "">("");
  const [yearFilter, setYearFilter] = useState("");
  const [invoicePage, setInvoicePage] = useState(1);
  const [paymentPage, setPaymentPage] = useState(1);
  const [submissionOpen, setSubmissionOpen] = useState(false);
  const [submissionInvoiceId, setSubmissionInvoiceId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const deferredInvoiceSearch = useDeferredValue(invoiceSearch);
  const invoices = account?.invoices ?? EMPTY_INVOICES;
  const payments = account?.payments ?? EMPTY_PAYMENTS;
  const openInvoices = invoices.filter(
    (invoice) => invoice.document_state === "issued" && Number(invoice.outstanding_amount) > 0,
  );

  const invoiceYears = useMemo(
    () =>
      Array.from(new Set(invoices.map((invoice) => billingYear(invoice.issued_at)))).sort(
        (left, right) => right.localeCompare(left),
      ),
    [invoices],
  );

  const filteredInvoices = useMemo(() => {
    const normalizedSearch = deferredInvoiceSearch.trim().toLocaleLowerCase("ru-RU");
    return invoices.filter(
      (invoice) =>
        (!normalizedSearch ||
          invoice.invoice_number.toLocaleLowerCase("ru-RU").includes(normalizedSearch)) &&
        (!statusFilter || financialInvoiceStatus(invoice) === statusFilter) &&
        (!yearFilter || billingYear(invoice.issued_at) === yearFilter),
    );
  }, [deferredInvoiceSearch, invoices, statusFilter, yearFilter]);

  const totalInvoicePages = Math.max(1, Math.ceil(filteredInvoices.length / PAGE_SIZE));
  const visibleInvoicePage = Math.min(invoicePage, totalInvoicePages);
  const visibleInvoices = filteredInvoices.slice(
    (visibleInvoicePage - 1) * PAGE_SIZE,
    visibleInvoicePage * PAGE_SIZE,
  );
  const totalPaymentPages = Math.max(1, Math.ceil(payments.length / PAGE_SIZE));
  const visiblePaymentPage = Math.min(paymentPage, totalPaymentPages);
  const visiblePayments = payments.slice(
    (visiblePaymentPage - 1) * PAGE_SIZE,
    visiblePaymentPage * PAGE_SIZE,
  );
  const selectedInvoice =
    account?.invoices.find((invoice) => invoice.invoice_id === openInvoiceId) ?? null;
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
        description="Текущий тариф, задолженность, счета и подтвержденные платежи аптеки."
        actions={
          canCreatePaymentSubmission ? (
            <Button
              size="sm"
              disabled={!online || openInvoices.length === 0}
              onClick={() => {
                setSubmissionInvoiceId(openInvoices[0]?.invoice_id ?? null);
                setSubmissionOpen(true);
              }}
            >
              Сообщить об оплате
            </Button>
          ) : undefined
        }
      />

      {!online && (canCreatePaymentSubmission || canWithdrawPaymentSubmission) ? (
        <div
          className="rounded-lg border border-warning/30 bg-warning-subtle px-4 py-3 text-sm text-warning-foreground"
          role="status"
        >
          Нет подключения. Данные можно просматривать, отправка и отзыв заявок временно отключены.
        </div>
      ) : null}

      {notice ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-info/30 bg-info-subtle px-4 py-3 text-sm text-info-foreground"
          role="status"
        >
          <span>{notice}</span>
          <Button size="sm" variant="ghost" onClick={() => setNotice(null)}>
            Закрыть
          </Button>
        </div>
      ) : null}

      {accountQuery.isLoading ? (
        <Card className="p-4" aria-label="Загрузка расчетов" aria-busy="true">
          <SkeletonRows rows={7} />
        </Card>
      ) : accountQuery.error && !account ? (
        <BillingError
          message={describeApiError(accountQuery.error, "Не удалось загрузить расчеты")}
          retrying={accountQuery.isFetching}
          onRetry={() => void accountQuery.refetch()}
        />
      ) : account ? (
        <>
          <BillingOverview
            account={account}
            fetching={accountQuery.isFetching}
            onRefresh={() => void accountQuery.refetch()}
            onOpenInvoice={setOpenInvoiceId}
            onShowHistory={showInvoiceHistory}
          />

          <section
            id="billing-invoices"
            className="min-w-0 space-y-3"
            aria-labelledby="billing-invoices-heading"
            aria-label="История счетов"
          >
            <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
              <div className="min-w-0">
                <h2
                  id="billing-invoices-heading"
                  className="text-base font-semibold text-foreground"
                >
                  История счетов
                </h2>
                <p className="mt-0.5 text-xs text-foreground-muted">
                  Сумма и остаток рассчитываются по подтвержденным финансовым операциям.
                </p>
              </div>
              <Badge tone="neutral" aria-live="polite">
                {invoiceCountLabel(filteredInvoices.length)}
              </Badge>
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
                          setStatusFilter(event.target.value as FinancialInvoiceDisplayStatus | "");
                          setInvoicePage(1);
                        }}
                        className="w-full sm:w-48"
                      >
                        <option value="">Все статусы</option>
                        {FILTER_STATUSES.map((status) => (
                          <option key={status} value={status}>
                            {financialInvoiceStatusLabel[status]}
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

            {filteredInvoices.length === 0 ? (
              <TableEmpty title={hasActiveFilters ? "Счета не найдены" : "Счетов пока нет"}>
                {hasActiveFilters
                  ? "Измените или сбросьте фильтры, чтобы увидеть другие счета."
                  : "Новые счета появятся здесь после выставления администрацией Aurum Pharma."}
              </TableEmpty>
            ) : (
              <>
                <InvoiceHistory invoices={visibleInvoices} onOpenInvoice={setOpenInvoiceId} />
                {filteredInvoices.length > PAGE_SIZE ? (
                  <Pagination
                    page={visibleInvoicePage}
                    pageSize={PAGE_SIZE}
                    total={filteredInvoices.length}
                    onPage={setInvoicePage}
                  />
                ) : null}
              </>
            )}
          </section>

          <PaymentSubmissionsPanel
            canWithdraw={canWithdrawPaymentSubmission}
            online={online}
            onNotice={setNotice}
          />

          <section className="min-w-0 space-y-3" aria-labelledby="billing-payments-heading">
            <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
              <div className="min-w-0">
                <h2
                  id="billing-payments-heading"
                  className="text-base font-semibold text-foreground"
                >
                  Подтвержденные платежи
                </h2>
                <p className="mt-0.5 text-xs text-foreground-muted">
                  Здесь отображаются только проверенные Aurum Pharma платежи.
                </p>
              </div>
              <Badge tone="neutral">{paymentCountLabel(payments.length)}</Badge>
            </div>
            {payments.length === 0 ? (
              <TableEmpty title="Подтвержденных платежей пока нет">
                Платеж появится после проверки сотрудником Aurum Pharma.
              </TableEmpty>
            ) : (
              <>
                <PaymentHistory payments={visiblePayments} />
                {payments.length > PAGE_SIZE ? (
                  <Pagination
                    page={visiblePaymentPage}
                    pageSize={PAGE_SIZE}
                    total={payments.length}
                    onPage={setPaymentPage}
                  />
                ) : null}
              </>
            )}
          </section>
        </>
      ) : null}

      <InvoiceDetailModal invoice={selectedInvoice} onClose={() => setOpenInvoiceId(null)} />
      {canCreatePaymentSubmission ? (
        <PaymentSubmissionModal
          open={submissionOpen}
          invoices={invoices}
          initialInvoiceId={submissionInvoiceId}
          online={online}
          onClose={() => {
            setSubmissionOpen(false);
            setSubmissionInvoiceId(null);
          }}
          onCompleted={() => {
            setSubmissionOpen(false);
            setSubmissionInvoiceId(null);
            setNotice("Подтверждение оплаты отправлено в Aurum Pharma.");
          }}
          onRefreshRequired={setNotice}
        />
      ) : null}
    </div>
  );
}

function InvoiceHistory({
  invoices,
  onOpenInvoice,
}: {
  invoices: readonly TenantFinancialInvoice[];
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
              <TH className="text-right">Остаток</TH>
              <TH>Статус</TH>
              <TH>
                <span className="sr-only">Действие</span>
              </TH>
            </TR>
          </THead>
          <TBody>
            {invoices.map((invoice) => {
              const status = financialInvoiceStatus(invoice);
              return (
                <TR key={invoice.invoice_id}>
                  <TD className="whitespace-nowrap font-mono text-xs font-semibold text-primary">
                    {invoice.invoice_number}
                  </TD>
                  <TD className="whitespace-nowrap">{formatBillingDate(invoice.issued_at)}</TD>
                  <TD className="whitespace-nowrap">{formatBillingDate(invoice.due_at)}</TD>
                  <TD className="whitespace-nowrap text-right tabular-nums">
                    {formatBillingMoney(invoice.total_amount, invoice.currency)}
                  </TD>
                  <TD className="whitespace-nowrap text-right font-semibold tabular-nums">
                    {formatBillingMoney(invoice.outstanding_amount, invoice.currency)}
                  </TD>
                  <TD>
                    <Badge tone={financialInvoiceStatusTone[status]}>
                      {financialInvoiceStatusLabel[status]}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-primary"
                      onClick={() => onOpenInvoice(invoice.invoice_id)}
                      aria-label={`Открыть счет ${invoice.invoice_number}`}
                    >
                      Открыть
                    </Button>
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>
      </div>

      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface md:hidden">
        {invoices.map((invoice) => {
          const status = financialInvoiceStatus(invoice);
          return (
            <li key={invoice.invoice_id}>
              <button
                type="button"
                className="block min-h-24 w-full px-4 py-3 text-left transition-colors duration-fast hover:bg-foreground/[0.025] active:bg-foreground/5"
                onClick={() => onOpenInvoice(invoice.invoice_id)}
                aria-label={`Открыть счет ${invoice.invoice_number}`}
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
                  <Badge tone={financialInvoiceStatusTone[status]}>
                    {financialInvoiceStatusLabel[status]}
                  </Badge>
                </span>
                <span className="mt-3 flex items-end justify-between gap-3">
                  <span className="text-xs text-foreground-muted">Остаток</span>
                  <span className="text-lg font-semibold tabular-nums text-foreground">
                    {formatBillingMoney(invoice.outstanding_amount, invoice.currency)}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function PaymentHistory({ payments }: { payments: readonly TenantBillingPayment[] }): JSX.Element {
  return (
    <>
      <div className="hidden md:block">
        <Table>
          <THead>
            <TR>
              <TH>Дата оплаты</TH>
              <TH>Подтвержден</TH>
              <TH className="text-right">Сумма</TH>
              <TH className="text-right">Зачтено в счета</TH>
              <TH className="text-right">Аванс</TH>
              <TH>Состояние</TH>
            </TR>
          </THead>
          <TBody>
            {payments.map((payment, index) => (
              <TR key={`${payment.confirmed_at}-${payment.amount}-${index}`}>
                <TD className="whitespace-nowrap">{formatBillingDate(payment.paid_at)}</TD>
                <TD className="whitespace-nowrap">{formatBillingDateTime(payment.confirmed_at)}</TD>
                <TD className="whitespace-nowrap text-right font-semibold tabular-nums">
                  {formatBillingMoney(payment.amount, payment.currency)}
                </TD>
                <TD className="whitespace-nowrap text-right tabular-nums">
                  {formatBillingMoney(payment.allocated_amount, payment.currency)}
                </TD>
                <TD className="whitespace-nowrap text-right tabular-nums">
                  {formatBillingMoney(payment.credit_amount, payment.currency)}
                </TD>
                <TD>
                  <Badge tone={payment.lifecycle_state === "confirmed" ? "success" : "neutral"}>
                    {payment.lifecycle_state === "confirmed" ? "Подтвержден" : "Скорректирован"}
                  </Badge>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </div>

      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface md:hidden">
        {payments.map((payment, index) => (
          <li key={`${payment.confirmed_at}-${payment.amount}-${index}`} className="px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs text-foreground-muted">
                  {formatBillingDate(payment.paid_at)}
                </p>
                <p className="mt-1 text-lg font-semibold tabular-nums text-foreground">
                  {formatBillingMoney(payment.amount, payment.currency)}
                </p>
              </div>
              <Badge tone={payment.lifecycle_state === "confirmed" ? "success" : "neutral"}>
                {payment.lifecycle_state === "confirmed" ? "Подтвержден" : "Скорректирован"}
              </Badge>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-border pt-3 text-sm">
              <div>
                <dt className="text-xs text-foreground-muted">Зачтено</dt>
                <dd className="mt-1 tabular-nums">
                  {formatBillingMoney(payment.allocated_amount, payment.currency)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-foreground-muted">Аванс</dt>
                <dd className="mt-1 tabular-nums">
                  {formatBillingMoney(payment.credit_amount, payment.currency)}
                </dd>
              </div>
            </dl>
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
  return `${count} ${pluralize(count, "счет", "счета", "счетов")}`;
}

function paymentCountLabel(count: number): string {
  return `${count} ${pluralize(count, "платеж", "платежа", "платежей")}`;
}

function pluralize(count: number, one: string, few: string, many: string): string {
  const absolute = Math.abs(count) % 100;
  const lastDigit = absolute % 10;
  if (absolute > 10 && absolute < 20) return many;
  if (lastDigit === 1) return one;
  if (lastDigit >= 2 && lastDigit <= 4) return few;
  return many;
}
