import { useEffect, useMemo, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
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
import { hasPlatformCapability, PLATFORM_CAPABILITIES } from "@/features/auth/platformCapabilities";
import { formatBillingDate, formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";

import { usePlatformBillingOverview, usePlatformInvoices } from "./queries";
import {
  type PlatformBillingOverview,
  type PlatformInvoice,
  type PlatformInvoiceStatus,
} from "./types";

const PAGE_SIZE = 20;
const SEARCH_DELAY_MS = 350;

const invoiceStatusLabel: Record<PlatformInvoiceStatus, string> = {
  pending: "Ожидает оплаты",
  overdue: "Просрочен",
  paid: "Оплачен",
  cancelled: "Отменён",
};

const invoiceStatusTone: Record<PlatformInvoiceStatus, "info" | "danger" | "success" | "neutral"> =
  {
    pending: "info",
    overdue: "danger",
    paid: "success",
    cancelled: "neutral",
  };

const subscriptionStatusLabel: Record<string, string> = {
  trial: "Пробный",
  active: "Активна",
  grace_period: "Льготный период",
  suspended: "Приостановлена",
  cancelled: "Отменена",
  archived: "В архиве",
};

export function PlatformBillingPage(): JSX.Element {
  const { user } = useAuth();
  const canView = hasPlatformCapability(user, PLATFORM_CAPABILITIES.billingView);
  const preferenceKey = useFilterPreferenceKey("platform-billing");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<PlatformInvoiceStatus | "all">("all");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const normalized = searchInput.trim();
      if (normalized === search) return;
      setSearch(normalized);
      setPage(1);
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search, searchInput]);

  const filters = useMemo(
    () => ({
      q: search || undefined,
      status: status === "all" ? undefined : status,
      page,
      page_size: PAGE_SIZE,
    }),
    [page, search, status],
  );
  const overview = usePlatformBillingOverview(canView);
  const invoices = usePlatformInvoices(filters, canView);

  useEffect(() => {
    const total = invoices.data?.total;
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [invoices.data?.total, page]);

  if (!canView) {
    return (
      <AccessDeniedCard
        title="Расчёты Aurum"
        message="У вас нет доступа к финансовой сводке платформы."
        fallbackTo="/admin"
        fallbackLabel="Центр управления"
      />
    );
  }

  const resetFilters = () => {
    setSearchInput("");
    setSearch("");
    setStatus("all");
    setPage(1);
  };
  const refresh = () => {
    void Promise.all([overview.refetch(), invoices.refetch()]);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Расчёты Aurum"
        description="Состояние подписок и счетов аптек"
        meta={
          overview.data ? <>данные на {formatDateTime(overview.data.generated_at)}</> : undefined
        }
        actions={
          <Button
            variant="secondary"
            size="sm"
            isLoading={overview.isFetching || invoices.isFetching}
            onClick={refresh}
          >
            Обновить
          </Button>
        }
      />

      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-info/25 bg-info-subtle px-3 py-2 text-sm text-info-foreground">
        <Badge tone="info">Только чтение</Badge>
        <span>
          Предварительная сводка текущих счетов. Финансовые решения отключены до перехода на
          защищённый журнал.
        </span>
      </div>

      {overview.isLoading ? (
        <OverviewSkeleton />
      ) : overview.error && !overview.data ? (
        <ReadError
          message={describeApiError(overview.error, "Не удалось загрузить финансовую сводку")}
          retrying={overview.isFetching}
          onRetry={() => void overview.refetch()}
        />
      ) : overview.data ? (
        <OverviewStrip overview={overview.data} />
      ) : null}

      {overview.data && overview.data.overdue_invoices > 0 ? (
        <div
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3"
          role="status"
        >
          <p className="font-semibold text-danger-foreground">
            Просрочено: {invoiceCountLabel(overview.data.overdue_invoices)}
          </p>
          <p className="mt-1 text-sm text-danger-foreground">
            Реестр ниже сначала показывает счета с самым ранним сроком.
          </p>
        </div>
      ) : null}

      <section className="space-y-3" aria-labelledby="platform-invoices-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="platform-invoices-heading" className="text-base font-semibold text-foreground">
              Реестр счетов
            </h2>
            <p className="mt-0.5 text-xs text-foreground-muted">
              Поиск не сохраняет финансовые данные в браузере.
            </p>
          </div>
          {invoices.data ? (
            <Badge tone="neutral">{invoiceCountLabel(invoices.data.total)}</Badge>
          ) : null}
        </div>

        <ConfigurableFilterBar
          preferenceKey={preferenceKey}
          onResetValues={resetFilters}
          filters={[
            {
              id: "search",
              label: "Поиск",
              alwaysVisible: true,
              active: Boolean(searchInput),
              onClear: () => {
                setSearchInput("");
                setSearch("");
                setPage(1);
              },
              content: (
                <div>
                  <Label htmlFor="platform-billing-search">Аптека или номер счёта</Label>
                  <Input
                    id="platform-billing-search"
                    type="search"
                    autoComplete="off"
                    placeholder="Название аптеки или INV-..."
                    value={searchInput}
                    onChange={(event) => setSearchInput(event.target.value)}
                    className="w-full sm:w-72"
                  />
                </div>
              ),
            },
            {
              id: "status",
              label: "Статус",
              defaultVisible: true,
              active: status !== "all",
              onClear: () => {
                setStatus("all");
                setPage(1);
              },
              content: (
                <div>
                  <Label htmlFor="platform-billing-status">Статус</Label>
                  <Select
                    id="platform-billing-status"
                    value={status}
                    onChange={(event) => {
                      setStatus(event.target.value as PlatformInvoiceStatus | "all");
                      setPage(1);
                    }}
                    className="w-full sm:w-52"
                  >
                    <option value="all">Все статусы</option>
                    <option value="overdue">Просроченные</option>
                    <option value="pending">Ожидают оплаты</option>
                    <option value="paid">Оплаченные</option>
                    <option value="cancelled">Отменённые</option>
                  </Select>
                </div>
              ),
            },
          ]}
        />

        {invoices.isLoading ? (
          <SkeletonRows rows={7} />
        ) : invoices.error && !invoices.data ? (
          <ReadError
            message={describeApiError(invoices.error, "Не удалось загрузить счета")}
            retrying={invoices.isFetching}
            onRetry={() => void invoices.refetch()}
          />
        ) : invoices.data?.items.length === 0 ? (
          <TableEmpty title="Счета не найдены">Измените или сбросьте фильтры.</TableEmpty>
        ) : invoices.data ? (
          <>
            <InvoiceRegistry invoices={invoices.data.items} />
            {invoices.data.total > PAGE_SIZE ? (
              <Pagination
                page={invoices.data.page}
                pageSize={PAGE_SIZE}
                total={invoices.data.total}
                onPage={setPage}
              />
            ) : null}
          </>
        ) : null}
      </section>
    </div>
  );
}

function OverviewStrip({ overview }: { overview: PlatformBillingOverview }): JSX.Element {
  const metrics = [
    ["Аптек", overview.tenants_total],
    ["Активных подписок", overview.active_subscriptions],
    ["Требуют внимания", overview.attention_subscriptions],
    ["Открытых счетов", overview.open_invoices],
    ["Просрочено", overview.overdue_invoices],
    ["Остаток по счетам", formatBillingMoney(overview.outstanding_amount, overview.currency)],
  ] as const;
  return (
    <Card aria-label="Сводка расчётов">
      <dl className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        {metrics.map(([label, value], index) => (
          <div
            key={label}
            className={`min-w-0 border-border px-4 py-3 ${overviewMetricBorders(index)}`}
          >
            <dt className="text-xs text-foreground-muted">{label}</dt>
            <dd className="mt-2 break-words text-lg font-semibold tabular-nums text-foreground">
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function overviewMetricBorders(index: number): string {
  const mobile = `${index < 4 ? "border-b" : "border-b-0"} ${index % 2 === 0 ? "border-r" : "border-r-0"}`;
  const tablet = `${index < 3 ? "md:border-b" : "md:border-b-0"} ${index % 3 === 2 ? "md:border-r-0" : "md:border-r"}`;
  const desktop = `${index < 5 ? "xl:border-r" : "xl:border-r-0"} xl:border-b-0`;
  return `${mobile} ${tablet} ${desktop}`;
}

function OverviewSkeleton(): JSX.Element {
  return (
    <Card aria-label="Загрузка сводки расчётов" aria-busy="true">
      <div className="grid grid-cols-2 gap-4 p-4 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="space-y-2">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-6 w-20" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function InvoiceRegistry({ invoices }: { invoices: readonly PlatformInvoice[] }): JSX.Element {
  return (
    <>
      <div className="hidden md:block">
        <Table aria-label="Счета аптек">
          <THead>
            <TR>
              <TH>Аптека</TH>
              <TH>Счёт</TH>
              <TH>Срок</TH>
              <TH className="text-right">Сумма</TH>
              <TH className="text-right">Остаток</TH>
              <TH>Подписка</TH>
              <TH>Статус</TH>
            </TR>
          </THead>
          <TBody>
            {invoices.map((invoice) => (
              <TR key={invoice.invoice_number}>
                <TD className="max-w-64 font-medium">{invoice.tenant_name}</TD>
                <TD className="whitespace-nowrap font-mono text-xs text-primary">
                  {invoice.invoice_number}
                </TD>
                <TD className="whitespace-nowrap">{formatBillingDate(invoice.due_at)}</TD>
                <TD className="whitespace-nowrap text-right tabular-nums">
                  {formatBillingMoney(invoice.amount, invoice.currency)}
                </TD>
                <TD className="whitespace-nowrap text-right font-semibold tabular-nums">
                  {formatBillingMoney(invoice.outstanding_amount, invoice.currency)}
                </TD>
                <TD>{subscriptionStatusLabel[invoice.subscription_status] ?? "Неизвестно"}</TD>
                <TD>
                  <Badge tone={invoiceStatusTone[invoice.status]}>
                    {invoiceStatusLabel[invoice.status]}
                  </Badge>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </div>
      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface md:hidden">
        {invoices.map((invoice) => (
          <li key={invoice.invoice_number} className="min-w-0 px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="break-words font-medium text-foreground">{invoice.tenant_name}</p>
                <p className="mt-1 break-all font-mono text-xs text-primary">
                  {invoice.invoice_number}
                </p>
              </div>
              <Badge tone={invoiceStatusTone[invoice.status]} className="shrink-0">
                {invoiceStatusLabel[invoice.status]}
              </Badge>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-border pt-3 text-sm">
              <div>
                <dt className="text-xs text-foreground-muted">Остаток</dt>
                <dd className="mt-1 font-semibold tabular-nums">
                  {formatBillingMoney(invoice.outstanding_amount, invoice.currency)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-foreground-muted">Срок</dt>
                <dd className="mt-1">{formatBillingDate(invoice.due_at)}</dd>
              </div>
            </dl>
          </li>
        ))}
      </ul>
    </>
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

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
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

export default PlatformBillingPage;
