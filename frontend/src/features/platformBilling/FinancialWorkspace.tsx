import { useEffect, useMemo, useState } from "react";

import {
  Badge,
  Button,
  Card,
  Input,
  Label,
  Pagination,
  SkeletonRows,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { formatBillingDate, formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";

import { ApprovePaymentModal, RegisterPaymentModal } from "./FinancialPaymentModal";
import { PaymentAdjustmentDecisionModal, PaymentAdjustmentModal } from "./FinancialAdjustmentModal";
import { PaymentSubmissionQueue } from "./PaymentSubmissionQueue";
import { PaymentSubmissionReviewModal } from "./PaymentSubmissionReviewModal";
import {
  usePlatformBillingTenants,
  usePlatformFinancialAccount,
  usePlatformPaymentAdjustmentQueue,
  usePlatformPaymentApprovalQueue,
  usePlatformPaymentSubmissions,
} from "./queries";
import {
  type PlatformBillingTenant,
  type PlatformFinancialAccount,
  type PlatformFinancialInvoice,
  type PlatformPaymentAdjustmentQueueItem,
  type PlatformPaymentApprovalQueueItem,
  type PlatformPaymentHistoryItem,
  type PlatformPaymentSubmissionListItem,
} from "./types";
import { useOnlineStatus } from "./useOnlineStatus";

const PAGE_SIZE = 20;
const SEARCH_DELAY_MS = 350;

export function FinancialWorkspace({
  canReview,
  canApprove,
  canCreateAdjustment,
  canApproveAdjustment,
  refreshSignal,
  onFetchingChange,
}: {
  canReview: boolean;
  canApprove: boolean;
  canCreateAdjustment: boolean;
  canApproveAdjustment: boolean;
  refreshSignal: number;
  onFetchingChange?: (fetching: boolean) => void;
}): JSX.Element {
  const online = useOnlineStatus();
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [tenantPage, setTenantPage] = useState(1);
  const [queuePage, setQueuePage] = useState(1);
  const [adjustmentPage, setAdjustmentPage] = useState(1);
  const [submissionPage, setSubmissionPage] = useState(1);
  const [selectedTenant, setSelectedTenant] = useState<PlatformBillingTenant | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [approvalTarget, setApprovalTarget] = useState<PlatformPaymentApprovalQueueItem | null>(
    null,
  );
  const [adjustmentTarget, setAdjustmentTarget] = useState<PlatformPaymentHistoryItem | null>(null);
  const [adjustmentDecisionTarget, setAdjustmentDecisionTarget] =
    useState<PlatformPaymentAdjustmentQueueItem | null>(null);
  const [submissionTarget, setSubmissionTarget] =
    useState<PlatformPaymentSubmissionListItem | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const normalized = searchInput.trim();
      if (normalized === search) return;
      setSearch(normalized);
      setTenantPage(1);
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search, searchInput]);

  const tenantFilters = useMemo(
    () => ({ q: search || undefined, page: tenantPage, page_size: PAGE_SIZE }),
    [search, tenantPage],
  );
  const tenants = usePlatformBillingTenants(tenantFilters, true);
  const tenantId = selectedTenant?.tenant_id ?? "";
  const account = usePlatformFinancialAccount(tenantId, selectedTenant !== null);
  const queue = usePlatformPaymentApprovalQueue(
    tenantId,
    queuePage,
    PAGE_SIZE,
    canApprove && selectedTenant !== null,
  );
  const adjustmentQueue = usePlatformPaymentAdjustmentQueue(
    tenantId,
    adjustmentPage,
    PAGE_SIZE,
    canApproveAdjustment && selectedTenant !== null,
  );
  const submissionQueue = usePlatformPaymentSubmissions(
    tenantId,
    submissionPage,
    PAGE_SIZE,
    canReview && selectedTenant !== null,
  );
  const refetchTenants = tenants.refetch;
  const refetchAccount = account.refetch;
  const refetchQueue = queue.refetch;
  const refetchAdjustmentQueue = adjustmentQueue.refetch;
  const refetchSubmissionQueue = submissionQueue.refetch;

  useEffect(() => {
    onFetchingChange?.(
      tenants.isFetching ||
        account.isFetching ||
        queue.isFetching ||
        adjustmentQueue.isFetching ||
        submissionQueue.isFetching,
    );
  }, [
    account.isFetching,
    adjustmentQueue.isFetching,
    onFetchingChange,
    queue.isFetching,
    submissionQueue.isFetching,
    tenants.isFetching,
  ]);

  useEffect(() => {
    if (refreshSignal === 0) return;
    void Promise.all([
      refetchTenants(),
      ...(selectedTenant ? [refetchAccount()] : []),
      ...(selectedTenant && canApprove ? [refetchQueue()] : []),
      ...(selectedTenant && canApproveAdjustment ? [refetchAdjustmentQueue()] : []),
      ...(selectedTenant && canReview ? [refetchSubmissionQueue()] : []),
    ]);
  }, [
    canApprove,
    canApproveAdjustment,
    canReview,
    refetchAccount,
    refetchAdjustmentQueue,
    refetchQueue,
    refetchSubmissionQueue,
    refetchTenants,
    refreshSignal,
    selectedTenant,
  ]);

  useEffect(() => {
    const total = tenants.data?.total;
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (tenantPage > lastPage) setTenantPage(lastPage);
  }, [tenantPage, tenants.data?.total]);

  useEffect(() => {
    const total = queue.data?.total;
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (queuePage > lastPage) setQueuePage(lastPage);
  }, [queue.data?.total, queuePage]);

  useEffect(() => {
    const total = adjustmentQueue.data?.total;
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (adjustmentPage > lastPage) setAdjustmentPage(lastPage);
  }, [adjustmentPage, adjustmentQueue.data?.total]);

  useEffect(() => {
    const total = submissionQueue.data?.total;
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (submissionPage > lastPage) setSubmissionPage(lastPage);
  }, [submissionPage, submissionQueue.data?.total]);

  useEffect(() => {
    setQueuePage(1);
    setAdjustmentPage(1);
    setSubmissionPage(1);
    setApprovalTarget(null);
    setAdjustmentTarget(null);
    setAdjustmentDecisionTarget(null);
    setSubmissionTarget(null);
    setNotice(null);
  }, [tenantId]);

  const refreshFinancialData = (message: string) => {
    setNotice(message);
    void Promise.all([
      account.refetch(),
      ...(canApprove ? [queue.refetch()] : []),
      ...(canApproveAdjustment ? [adjustmentQueue.refetch()] : []),
      ...(canReview ? [submissionQueue.refetch()] : []),
    ]);
  };

  return (
    <section className="space-y-3" aria-labelledby="financial-workspace-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="financial-workspace-heading" className="text-base font-semibold text-foreground">
            Клиенты и оплаты
          </h2>
          <p className="mt-0.5 text-xs text-foreground-muted">
            Защищённый финансовый журнал и независимое подтверждение банковских платежей.
          </p>
        </div>
        {!canReview && !canApprove && !canCreateAdjustment && !canApproveAdjustment ? (
          <Badge tone="neutral">Только просмотр</Badge>
        ) : null}
      </div>

      {!online ? (
        <div
          className="rounded-lg border border-warning/30 bg-warning-subtle px-4 py-3 text-sm text-warning-foreground"
          role="status"
        >
          Нет подключения. Показанные данные можно просматривать, денежные действия отключены.
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

      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(16rem,20rem)_minmax(0,1fr)]">
        <TenantPicker
          query={searchInput}
          setQuery={setSearchInput}
          tenants={tenants}
          selectedTenantId={tenantId}
          onSelect={setSelectedTenant}
          page={tenantPage}
          onPage={setTenantPage}
        />

        <div className="min-w-0 space-y-4">
          {!selectedTenant ? (
            <TableEmpty title="Выберите аптеку">
              Найдите клиента слева, чтобы открыть его счета, платежи и очередь проверки.
            </TableEmpty>
          ) : account.isLoading ? (
            <SkeletonRows rows={7} />
          ) : account.error && !account.data ? (
            <ReadError
              message={describeApiError(account.error, "Не удалось загрузить финансовую карточку")}
              retrying={account.isFetching}
              onRetry={() => void account.refetch()}
            />
          ) : account.data ? (
            <>
              <FinancialAccountPanel
                tenant={selectedTenant}
                account={account.data}
                canReview={canReview}
                canCreateAdjustment={canCreateAdjustment}
                online={online}
                onRegister={() => setRegisterOpen(true)}
                onAdjust={setAdjustmentTarget}
              />
              {canReview ? (
                <PaymentSubmissionQueue
                  query={submissionQueue}
                  page={submissionPage}
                  onPage={setSubmissionPage}
                  online={online && account.data.journal_balanced}
                  onReview={setSubmissionTarget}
                />
              ) : null}
              {canApprove ? (
                <ApprovalQueuePanel
                  queue={queue}
                  page={queuePage}
                  onPage={setQueuePage}
                  online={online}
                  journalBalanced={account.data.journal_balanced}
                  onApprove={setApprovalTarget}
                />
              ) : null}
              {canApproveAdjustment ? (
                <AdjustmentQueuePanel
                  queue={adjustmentQueue}
                  page={adjustmentPage}
                  onPage={setAdjustmentPage}
                  online={online}
                  journalBalanced={account.data.journal_balanced}
                  onReview={setAdjustmentDecisionTarget}
                />
              ) : null}
            </>
          ) : null}
        </div>
      </div>

      {selectedTenant && account.data ? (
        <RegisterPaymentModal
          open={registerOpen}
          tenantId={selectedTenant.tenant_id}
          invoices={account.data.invoices}
          online={online && account.data.journal_balanced}
          onClose={() => setRegisterOpen(false)}
          onCompleted={(message) => {
            setRegisterOpen(false);
            refreshFinancialData(message);
          }}
          onRefreshRequired={refreshFinancialData}
        />
      ) : null}

      <ApprovePaymentModal
        item={approvalTarget}
        online={online && account.data?.journal_balanced === true}
        onClose={() => setApprovalTarget(null)}
        onCompleted={(result) => {
          setApprovalTarget(null);
          refreshFinancialData(
            result.access_restored
              ? `Платёж подтверждён. Доступ аптеки восстановлен, остаток долга ${formatBillingMoney(result.blocking_outstanding_amount, "TJS")}.`
              : `Платёж подтверждён. Остаток долга ${formatBillingMoney(result.blocking_outstanding_amount, "TJS")}.`,
          );
        }}
        onRejected={() => {
          setApprovalTarget(null);
          refreshFinancialData("Платёж отклонён и удалён из очереди.");
        }}
        onRefreshRequired={refreshFinancialData}
      />

      {canReview ? (
        <PaymentSubmissionReviewModal
          item={submissionTarget}
          online={online && account.data?.journal_balanced === true}
          onClose={() => setSubmissionTarget(null)}
          onReviewed={() => {
            setSubmissionTarget(null);
            refreshFinancialData("Заявка передана другому сотруднику на подтверждение платежа.");
          }}
          onRejected={() => {
            setSubmissionTarget(null);
            refreshFinancialData("Заявка клиента отклонена.");
          }}
          onRefreshRequired={refreshFinancialData}
        />
      ) : null}

      <PaymentAdjustmentModal
        tenantId={tenantId}
        payment={adjustmentTarget}
        online={online && account.data?.journal_balanced === true}
        onClose={() => setAdjustmentTarget(null)}
        onCompleted={() => {
          setAdjustmentTarget(null);
          refreshFinancialData("Запрос передан другому сотруднику на подтверждение.");
        }}
        onRefreshRequired={refreshFinancialData}
      />

      <PaymentAdjustmentDecisionModal
        item={adjustmentDecisionTarget}
        online={online && account.data?.journal_balanced === true}
        onClose={() => setAdjustmentDecisionTarget(null)}
        onApproved={(result) => {
          setAdjustmentDecisionTarget(null);
          refreshFinancialData(
            result.access_review_required
              ? "Корректировка подтверждена. Требуется проверить доступ аптеки."
              : "Корректировка подтверждена и отражена в журнале.",
          );
        }}
        onRejected={() => {
          setAdjustmentDecisionTarget(null);
          refreshFinancialData("Запрос корректировки отклонён.");
        }}
        onRefreshRequired={refreshFinancialData}
      />
    </section>
  );
}

function TenantPicker({
  query,
  setQuery,
  tenants,
  selectedTenantId,
  onSelect,
  page,
  onPage,
}: {
  query: string;
  setQuery: (value: string) => void;
  tenants: ReturnType<typeof usePlatformBillingTenants>;
  selectedTenantId: string;
  onSelect: (tenant: PlatformBillingTenant) => void;
  page: number;
  onPage: (page: number) => void;
}): JSX.Element {
  return (
    <Card className="min-w-0 self-start overflow-hidden">
      <div className="border-b border-border p-3">
        <Label htmlFor="financial-tenant-search">Аптека</Label>
        <Input
          id="financial-tenant-search"
          type="search"
          autoComplete="off"
          placeholder="Найти по названию"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {tenants.isLoading ? (
        <div className="p-3">
          <SkeletonRows rows={6} />
        </div>
      ) : tenants.error && !tenants.data ? (
        <div className="p-3">
          <ReadError
            message={describeApiError(tenants.error, "Не удалось загрузить аптеки")}
            retrying={tenants.isFetching}
            onRetry={() => void tenants.refetch()}
          />
        </div>
      ) : tenants.data?.items.length === 0 ? (
        <p className="p-4 text-sm text-foreground-muted">Аптеки не найдены.</p>
      ) : tenants.data ? (
        <>
          <ul
            className="max-h-[34rem] divide-y divide-border overflow-y-auto"
            aria-label="Аптеки для расчётов"
          >
            {tenants.data.items.map((tenant) => {
              const selected = tenant.tenant_id === selectedTenantId;
              return (
                <li key={tenant.tenant_id}>
                  <button
                    type="button"
                    aria-pressed={selected}
                    onClick={() => onSelect(tenant)}
                    className={`min-h-14 w-full px-3 py-2.5 text-left transition-colors duration-fast ${selected ? "bg-primary-subtle" : "hover:bg-foreground/[0.025]"}`}
                  >
                    <span className="block break-words text-sm font-semibold text-foreground">
                      {tenant.name}
                    </span>
                    <span className="mt-1 block text-xs text-foreground-muted">
                      {subscriptionStatusLabel(tenant.subscription_status)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {tenants.data.total > PAGE_SIZE ? (
            <div className="border-t border-border p-3">
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={tenants.data.total}
                onPage={onPage}
              />
            </div>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

function FinancialAccountPanel({
  tenant,
  account,
  canReview,
  canCreateAdjustment,
  online,
  onRegister,
  onAdjust,
}: {
  tenant: PlatformBillingTenant;
  account: PlatformFinancialAccount;
  canReview: boolean;
  canCreateAdjustment: boolean;
  online: boolean;
  onRegister: () => void;
  onAdjust: (payment: PlatformPaymentHistoryItem) => void;
}): JSX.Element {
  const openInvoices = account.invoices.filter(
    (invoice) => invoice.document_state === "issued" && Number(invoice.outstanding_amount) > 0,
  );
  const commandsEnabled = online && account.journal_balanced;
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <h3 className="break-words text-lg font-semibold text-foreground">{tenant.name}</h3>
            <p className="mt-1 text-xs text-foreground-muted">
              {tenantStatusLabel(tenant.tenant_status)}
            </p>
          </div>
          {canReview ? (
            <Button
              size="sm"
              disabled={!commandsEnabled || openInvoices.length === 0}
              onClick={onRegister}
            >
              Зарегистрировать оплату
            </Button>
          ) : null}
        </div>
        <dl className="grid grid-cols-2 md:grid-cols-3">
          <Metric
            label="Задолженность"
            value={formatBillingMoney(account.outstanding_amount, account.currency)}
            className="border-r md:border-r-0"
          />
          <Metric
            label="Кредит"
            value={formatBillingMoney(account.credit_balance, account.currency)}
          />
          <Metric
            label="Контроль журнала"
            value={account.journal_balanced ? "Сбалансирован" : "Нарушен"}
            className="col-span-2 border-t md:col-span-1 md:border-l md:border-t-0"
          />
        </dl>
      </Card>

      {!account.journal_balanced ? (
        <div
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm text-danger-foreground"
          role="alert"
        >
          Контроль финансового журнала не пройден. Денежные действия заблокированы до проверки
          системным администратором.
        </div>
      ) : null}

      <InvoicesPanel invoices={account.invoices} />
      <PaymentsPanel
        account={account}
        canCreateAdjustment={canCreateAdjustment}
        commandsEnabled={commandsEnabled}
        onAdjust={onAdjust}
      />
    </div>
  );
}

function Metric({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string;
  className?: string;
}): JSX.Element {
  return (
    <div className={`min-w-0 border-border px-4 py-3 ${className}`}>
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className="mt-2 break-words font-semibold tabular-nums text-foreground">{value}</dd>
    </div>
  );
}

function InvoicesPanel({
  invoices,
}: {
  invoices: readonly PlatformFinancialInvoice[];
}): JSX.Element {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-border px-4 py-3">
        <h3 className="font-semibold text-foreground">Счета</h3>
      </div>
      {invoices.length === 0 ? (
        <p className="p-4 text-sm text-foreground-muted">Финансовые счета ещё не выпущены.</p>
      ) : (
        <div className="overflow-x-auto">
          <Table aria-label="Финансовые счета аптеки">
            <THead>
              <TR>
                <TH>Счёт</TH>
                <TH>Период</TH>
                <TH>Срок</TH>
                <TH>Состояние</TH>
                <TH className="text-right">Остаток</TH>
              </TR>
            </THead>
            <TBody>
              {invoices.map((invoice) => (
                <TR key={invoice.invoice_id}>
                  <TD className="whitespace-nowrap font-medium text-primary">
                    {invoice.invoice_number}
                  </TD>
                  <TD className="whitespace-nowrap">
                    {formatBillingDate(invoice.period_start)} –{" "}
                    {formatBillingDate(invoice.period_end)}
                  </TD>
                  <TD className="whitespace-nowrap">{formatBillingDate(invoice.due_at)}</TD>
                  <TD>
                    <Badge tone={invoiceTone(invoice)}>{invoiceStatusLabel(invoice)}</Badge>
                  </TD>
                  <TD className="whitespace-nowrap text-right font-semibold tabular-nums">
                    {formatBillingMoney(invoice.outstanding_amount, invoice.currency)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </Card>
  );
}

function PaymentsPanel({
  account,
  canCreateAdjustment,
  commandsEnabled,
  onAdjust,
}: {
  account: PlatformFinancialAccount;
  canCreateAdjustment: boolean;
  commandsEnabled: boolean;
  onAdjust: (payment: PlatformPaymentHistoryItem) => void;
}): JSX.Element {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-border px-4 py-3">
        <h3 className="font-semibold text-foreground">Подтверждённые платежи</h3>
      </div>
      {account.payments.length === 0 ? (
        <p className="p-4 text-sm text-foreground-muted">Подтверждённых платежей пока нет.</p>
      ) : (
        <div className="overflow-x-auto">
          <Table aria-label="Подтверждённые платежи аптеки">
            <THead>
              <TR>
                <TH>Дата</TH>
                <TH className="text-right">Сумма</TH>
                <TH className="text-right">Распределено</TH>
                <TH className="text-right">В кредит</TH>
                <TH>Состояние</TH>
                {canCreateAdjustment ? <TH className="text-right">Действие</TH> : null}
              </TR>
            </THead>
            <TBody>
              {account.payments.map((payment) => (
                <TR key={payment.payment_id}>
                  <TD className="whitespace-nowrap">{formatDateTime(payment.paid_at)}</TD>
                  <TD className="text-right tabular-nums">
                    {formatBillingMoney(payment.amount, payment.currency)}
                  </TD>
                  <TD className="text-right tabular-nums">
                    {formatBillingMoney(payment.allocated_amount, payment.currency)}
                  </TD>
                  <TD className="text-right tabular-nums">
                    {formatBillingMoney(payment.credit_amount, payment.currency)}
                  </TD>
                  <TD>
                    <PaymentState payment={payment} />
                  </TD>
                  {canCreateAdjustment ? (
                    <TD className="text-right">
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={
                          !commandsEnabled ||
                          payment.adjustment_pending ||
                          Number(payment.reversible_amount) <= 0
                        }
                        onClick={() => onAdjust(payment)}
                      >
                        {payment.adjustment_pending ? "Ожидает решения" : "Корректировать"}
                      </Button>
                    </TD>
                  ) : null}
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </Card>
  );
}

function PaymentState({ payment }: { payment: PlatformPaymentHistoryItem }): JSX.Element {
  if (payment.adjustment_pending) return <Badge tone="warning">Ожидает решения</Badge>;
  if (payment.lifecycle_state === "reversed") {
    return (
      <Badge tone="neutral">
        {Number(payment.refunded_amount) > 0 ? "Возвращён" : "Скорректирован"}
      </Badge>
    );
  }
  if (Number(payment.refunded_amount) > 0) return <Badge tone="info">Частичный возврат</Badge>;
  return <Badge tone="success">Подтверждён</Badge>;
}

function ApprovalQueuePanel({
  queue,
  page,
  onPage,
  online,
  journalBalanced,
  onApprove,
}: {
  queue: ReturnType<typeof usePlatformPaymentApprovalQueue>;
  page: number;
  onPage: (page: number) => void;
  online: boolean;
  journalBalanced: boolean;
  onApprove: (item: PlatformPaymentApprovalQueueItem) => void;
}): JSX.Element {
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div>
          <h3 className="font-semibold text-foreground">Ожидают подтверждения</h3>
          <p className="mt-0.5 text-xs text-foreground-muted">
            Только независимая проверка другим сотрудником.
          </p>
        </div>
        {queue.data ? (
          <Badge tone={queue.data.total > 0 ? "warning" : "neutral"}>{queue.data.total}</Badge>
        ) : null}
      </div>
      {queue.isLoading ? (
        <div className="p-4">
          <SkeletonRows rows={3} />
        </div>
      ) : queue.error && !queue.data ? (
        <div className="p-4">
          <ReadError
            message={describeApiError(queue.error, "Не удалось загрузить очередь")}
            retrying={queue.isFetching}
            onRetry={() => void queue.refetch()}
          />
        </div>
      ) : queue.data?.items.length === 0 ? (
        <p className="p-4 text-sm text-foreground-muted">Очередь пуста.</p>
      ) : queue.data ? (
        <>
          <ul className="divide-y divide-border">
            {queue.data.items.map((item) => (
              <li
                key={item.review_id}
                className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="font-medium text-foreground">
                    {item.invoice_number} · {formatBillingMoney(item.amount, item.currency)}
                  </p>
                  <p className="mt-1 text-xs text-foreground-muted">
                    Платёж {formatDateTime(item.paid_at)}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={!online || !journalBalanced || item.is_own_review}
                  onClick={() => onApprove(item)}
                >
                  {item.is_own_review ? "Нужен другой сотрудник" : "Проверить"}
                </Button>
              </li>
            ))}
          </ul>
          {queue.data.total > PAGE_SIZE ? (
            <div className="border-t border-border p-3">
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={queue.data.total}
                onPage={onPage}
              />
            </div>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

function AdjustmentQueuePanel({
  queue,
  page,
  onPage,
  online,
  journalBalanced,
  onReview,
}: {
  queue: ReturnType<typeof usePlatformPaymentAdjustmentQueue>;
  page: number;
  onPage: (page: number) => void;
  online: boolean;
  journalBalanced: boolean;
  onReview: (item: PlatformPaymentAdjustmentQueueItem) => void;
}): JSX.Element {
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div>
          <h3 className="font-semibold text-foreground">Корректировки на проверке</h3>
          <p className="mt-0.5 text-xs text-foreground-muted">
            Возвраты и исправления подтверждает второй сотрудник.
          </p>
        </div>
        {queue.data ? (
          <Badge tone={queue.data.total > 0 ? "warning" : "neutral"}>{queue.data.total}</Badge>
        ) : null}
      </div>
      {queue.isLoading ? (
        <div className="p-4">
          <SkeletonRows rows={3} />
        </div>
      ) : queue.error && !queue.data ? (
        <div className="p-4">
          <ReadError
            message={describeApiError(queue.error, "Не удалось загрузить корректировки")}
            retrying={queue.isFetching}
            onRetry={() => void queue.refetch()}
          />
        </div>
      ) : queue.data?.items.length === 0 ? (
        <p className="p-4 text-sm text-foreground-muted">Очередь пуста.</p>
      ) : queue.data ? (
        <>
          <ul className="divide-y divide-border">
            {queue.data.items.map((item) => (
              <li
                key={item.adjustment_id}
                className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="font-medium text-foreground">
                    {item.adjustment_kind === "bank_refund" ? "Возврат" : "Корректировка"} ·{" "}
                    {formatBillingMoney(item.amount, item.currency)}
                  </p>
                  <p className="mt-1 text-xs text-foreground-muted">
                    Исходный платёж {formatBillingMoney(item.payment_amount, item.currency)}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={!online || !journalBalanced || item.is_own_request}
                  onClick={() => onReview(item)}
                >
                  {item.is_own_request ? "Нужен другой сотрудник" : "Проверить"}
                </Button>
              </li>
            ))}
          </ul>
          {queue.data.total > PAGE_SIZE ? (
            <div className="border-t border-border p-3">
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={queue.data.total}
                onPage={onPage}
              />
            </div>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

function ReadError({
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
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3"
      role="alert"
    >
      <p className="text-sm text-danger-foreground">{message}</p>
      <Button variant="secondary" size="sm" isLoading={retrying} onClick={onRetry}>
        Повторить
      </Button>
    </div>
  );
}

function tenantStatusLabel(status: string): string {
  return (
    (
      {
        setup: "Настройка",
        trial: "Пробный период",
        active: "Активна",
        grace_period: "Льготный период",
        readonly: "Только чтение",
        archived: "В архиве",
      } as Record<string, string>
    )[status] ?? "Статус не определён"
  );
}

function subscriptionStatusLabel(status: string | null): string {
  if (!status) return "Подписка не создана";
  return (
    (
      {
        trial: "Пробная подписка",
        active: "Подписка активна",
        grace_period: "Льготный период",
        suspended: "Подписка приостановлена",
        cancelled: "Подписка отменена",
        archived: "Подписка в архиве",
      } as Record<string, string>
    )[status] ?? "Статус подписки не определён"
  );
}

function invoiceStatusLabel(invoice: PlatformFinancialInvoice): string {
  if (invoice.document_state === "void") return "Аннулирован";
  if (invoice.settlement_state === "paid") return "Оплачен";
  if (invoice.settlement_state === "partially_paid") return "Частично оплачен";
  if (invoice.collection_state === "overdue") return "Просрочен";
  return "Ожидает оплаты";
}

function invoiceTone(
  invoice: PlatformFinancialInvoice,
): "neutral" | "info" | "success" | "warning" | "danger" {
  if (invoice.document_state === "void") return "neutral";
  if (invoice.settlement_state === "paid") return "success";
  if (invoice.settlement_state === "partially_paid") return "warning";
  return invoice.collection_state === "overdue" ? "danger" : "info";
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" }).format(
    new Date(value),
  );
}
