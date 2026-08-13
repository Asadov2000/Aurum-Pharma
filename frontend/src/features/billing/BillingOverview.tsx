import { useMemo } from "react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Skeleton } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { formatBillingDate, formatBillingMoney } from "./format";
import {
  billingPeriodLabel,
  invoiceStatusLabel,
  invoiceStatusTone,
  subscriptionStatusLabel,
  subscriptionStatusTone,
} from "./labels";
import { type Invoice, type SubscriptionStatus, type SubscriptionWithPlan } from "./types";

interface BillingOverviewProps {
  subscription: SubscriptionWithPlan | null | undefined;
  invoices: readonly Invoice[];
  subscriptionLoading: boolean;
  subscriptionFetching: boolean;
  subscriptionError: unknown;
  invoicesLoading: boolean;
  invoicesFetching: boolean;
  invoicesError: unknown;
  onRetrySubscription: () => void;
  onRetryInvoices: () => void;
  onOpenInvoice: (invoiceId: string) => void;
  onShowHistory: () => void;
}

interface InvoiceSummary {
  outstandingCount: number;
  focusInvoice: Invoice | null;
  recentInvoices: readonly Invoice[];
}

type BannerTone = "neutral" | "success" | "warning" | "danger" | "info";

interface OverviewBanner {
  tone: BannerTone;
  eyebrow: string;
  title: string;
  description: string;
  invoice: Invoice | null;
}

const accessStatusLabel: Record<SubscriptionStatus, string> = {
  trial: "Пробный период",
  active: "Активен",
  grace_period: "Льготный период",
  suspended: "Приостановлен",
  cancelled: "Отменён",
  archived: "В архиве",
};

const accessStatusTone: Record<SubscriptionStatus, BannerTone> = {
  trial: "info",
  active: "success",
  grace_period: "warning",
  suspended: "danger",
  cancelled: "neutral",
  archived: "neutral",
};

const bannerClasses: Record<BannerTone, string> = {
  neutral: "border-border bg-surface text-foreground",
  success: "border-success/30 bg-success-subtle text-success-foreground",
  warning: "border-warning/35 bg-warning-subtle text-warning-foreground",
  danger: "border-danger/35 bg-danger-subtle text-danger-foreground",
  info: "border-info/35 bg-info-subtle text-info-foreground",
};

export function BillingOverview({
  subscription,
  invoices,
  subscriptionLoading,
  subscriptionFetching,
  subscriptionError,
  invoicesLoading,
  invoicesFetching,
  invoicesError,
  onRetrySubscription,
  onRetryInvoices,
  onOpenInvoice,
  onShowHistory,
}: BillingOverviewProps): JSX.Element {
  const summary = useMemo(() => summarizeInvoices(invoices), [invoices]);
  const banner = buildOverviewBanner(subscription, summary);
  const bannerInvoice = banner.invoice;

  return (
    <section className="space-y-4" aria-label="Сводка по тарифу и оплате">
      <Card aria-label="Ключевые показатели" aria-busy={subscriptionLoading || invoicesLoading}>
        <dl className="grid grid-cols-2 sm:grid-cols-4">
          <SummaryMetric
            className="border-b border-r border-border sm:border-b-0"
            label="Подписка"
            value={
              subscriptionLoading
                ? undefined
                : subscriptionError
                  ? "Недоступно"
                  : subscription
                    ? accessStatusLabel[subscription.status]
                    : "Не подключён"
            }
            tone={subscription ? accessStatusTone[subscription.status] : "neutral"}
          />
          <SummaryMetric
            className="border-b border-border sm:border-b-0 sm:border-r"
            label="Текущий период до"
            value={
              subscriptionLoading
                ? undefined
                : subscriptionError
                  ? "Недоступно"
                  : subscription
                    ? formatBillingDate(subscription.period_end)
                    : "Нет данных"
            }
          />
          <SummaryMetric
            className="border-r border-border"
            label="Открытые счета"
            value={
              invoicesLoading
                ? undefined
                : invoicesError
                  ? "Недоступно"
                  : invoiceCountLabel(summary.outstandingCount)
            }
            emphasis={!invoicesLoading && !invoicesError && summary.outstandingCount > 0}
          />
          <SummaryMetric
            label="Ближайший срок"
            value={
              invoicesLoading
                ? undefined
                : invoicesError
                  ? "Ошибка загрузки"
                  : summary.focusInvoice
                    ? formatBillingDate(summary.focusInvoice.due_at)
                    : "Открытых нет"
            }
            tone={
              invoicesError
                ? "danger"
                : summary.focusInvoice?.status === "overdue"
                  ? "danger"
                  : summary.focusInvoice
                    ? "warning"
                    : "success"
            }
          />
        </dl>
      </Card>

      {invoicesLoading ? (
        <div
          className="flex min-h-24 items-center rounded-lg border border-border bg-surface px-4 py-3"
          role="status"
        >
          <span className="sr-only">Загрузка состояния расчётов…</span>
          <div className="w-full space-y-2" aria-hidden="true">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-5 w-64 max-w-full" />
            <Skeleton className="h-4 w-96 max-w-full" />
          </div>
        </div>
      ) : invoicesError ? (
        <OverviewError
          message={describeApiError(invoicesError, "Не удалось загрузить состояние расчётов")}
          retrying={invoicesFetching}
          onRetry={onRetryInvoices}
        />
      ) : (
        <div
          className={`flex min-w-0 flex-col gap-4 rounded-lg border px-4 py-3 sm:flex-row sm:items-center sm:justify-between ${bannerClasses[banner.tone]}`}
          aria-live="polite"
        >
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase text-current opacity-75">
              {banner.eyebrow}
            </p>
            <h2 className="mt-1 text-base font-semibold text-current">{banner.title}</h2>
            <p className="mt-1 text-sm leading-5 text-current opacity-85">{banner.description}</p>
          </div>
          {bannerInvoice ? (
            <Button
              className="w-full sm:w-auto"
              variant={banner.tone === "danger" ? "danger" : "primary"}
              onClick={() => onOpenInvoice(bannerInvoice.id)}
              aria-label={`Открыть счёт ${bannerInvoice.invoice_number}`}
            >
              Открыть счёт
            </Button>
          ) : null}
        </div>
      )}

      <div className="grid min-w-0 gap-4 xl:grid-cols-3">
        <Card aria-labelledby="billing-next-payment-heading">
          <CardHeader>
            <CardTitle id="billing-next-payment-heading">Ближайший открытый счёт</CardTitle>
          </CardHeader>
          <CardContent className="min-h-48">
            {invoicesLoading ? (
              <OverviewCardSkeleton label="Загрузка ближайшего платежа…" />
            ) : invoicesError ? (
              <UnavailableCopy>Сводка появится после загрузки счетов.</UnavailableCopy>
            ) : (
              <div className="flex h-full flex-col">
                {summary.focusInvoice ? (
                  <>
                    <p className="font-display text-3xl font-semibold tabular-nums text-foreground">
                      {formatBillingMoney(
                        summary.focusInvoice.amount,
                        summary.focusInvoice.currency,
                      )}
                    </p>
                    <p className="mt-1 text-sm text-foreground-muted">Полная сумма счёта</p>
                    <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-border pt-4">
                      <BillingField label="Счёт" value={summary.focusInvoice.invoice_number} mono />
                      <BillingField
                        label={
                          summary.focusInvoice.status === "overdue" ? "Просрочен с" : "Срок оплаты"
                        }
                        value={formatBillingDate(summary.focusInvoice.due_at)}
                      />
                    </dl>
                  </>
                ) : (
                  <div>
                    <p className="text-base font-semibold text-foreground">Открытых счетов нет</p>
                    <p className="mt-2 text-sm leading-5 text-success-foreground">
                      Нет счетов, ожидающих оплаты.
                    </p>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <Card aria-labelledby="billing-current-plan-heading">
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle id="billing-current-plan-heading">Текущий тариф</CardTitle>
            {subscription ? (
              <Badge tone={subscriptionStatusTone[subscription.status]}>
                {subscriptionStatusLabel[subscription.status]}
              </Badge>
            ) : null}
          </CardHeader>
          <CardContent className="min-h-48">
            {subscriptionLoading ? (
              <OverviewCardSkeleton label="Загрузка тарифа…" />
            ) : subscriptionError ? (
              <OverviewError
                compact
                message={describeApiError(subscriptionError, "Не удалось загрузить подписку")}
                retrying={subscriptionFetching}
                onRetry={onRetrySubscription}
              />
            ) : subscription ? (
              <div>
                <p className="break-words text-xl font-semibold text-foreground">
                  {subscription.plan_name}
                </p>
                <p className="mt-0.5 break-all font-mono text-xs text-foreground-muted">
                  {subscription.plan_code}
                </p>
                <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-4 border-t border-border pt-4">
                  <BillingField
                    label="Оплата"
                    value={billingPeriodLabel[subscription.billing_period]}
                  />
                  <BillingField label="Аптечных точек" value={subscription.branches_count} />
                  <BillingField
                    label="Стоимость периода"
                    value={formatBillingMoney(subscription.amount, subscription.currency)}
                  />
                  <BillingField
                    label="Действует до"
                    value={formatBillingDate(subscription.period_end)}
                  />
                </dl>
              </div>
            ) : (
              <div>
                <p className="text-base font-semibold text-foreground">Подписка не подключена</p>
                <p className="mt-2 text-sm leading-6 text-foreground-muted">
                  Свяжитесь с поддержкой Aurum Pharma, чтобы активировать обслуживание аптеки.
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        <Card aria-labelledby="billing-recent-invoices-heading">
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle id="billing-recent-invoices-heading">Последние счета</CardTitle>
            {!invoicesLoading && !invoicesError && invoices.length > 0 ? (
              <Button variant="ghost" size="sm" className="text-primary" onClick={onShowHistory}>
                Вся история
              </Button>
            ) : null}
          </CardHeader>
          <CardContent className="min-h-48 p-0">
            {invoicesLoading ? (
              <div className="px-[var(--panel-padding-x)] py-[var(--panel-padding-y)]">
                <OverviewCardSkeleton label="Загрузка последних счетов…" />
              </div>
            ) : invoicesError ? (
              <div className="px-[var(--panel-padding-x)] py-[var(--panel-padding-y)]">
                <UnavailableCopy>Список появится после загрузки счетов.</UnavailableCopy>
              </div>
            ) : summary.recentInvoices.length === 0 ? (
              <div className="px-[var(--panel-padding-x)] py-[var(--panel-padding-y)]">
                <UnavailableCopy>Счетов пока нет.</UnavailableCopy>
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {summary.recentInvoices.map((invoice) => (
                  <li key={invoice.id}>
                    <button
                      type="button"
                      className="flex min-h-16 w-full items-center justify-between gap-3 px-[var(--panel-padding-x)] py-3 text-left transition-colors duration-fast hover:bg-foreground/[0.025] active:bg-foreground/5"
                      onClick={() => onOpenInvoice(invoice.id)}
                      aria-label={`Открыть счёт ${invoice.invoice_number}`}
                    >
                      <span className="min-w-0">
                        <span className="block break-all font-mono text-xs font-semibold text-primary">
                          {invoice.invoice_number}
                        </span>
                        <span className="mt-1 block text-xs text-foreground-muted">
                          {formatBillingDate(invoice.issued_at)} ·{" "}
                          {formatBillingMoney(invoice.amount, invoice.currency)}
                        </span>
                      </span>
                      <Badge tone={invoiceStatusTone[invoice.status]} className="shrink-0">
                        {invoiceStatusLabel[invoice.status]}
                      </Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

function SummaryMetric({
  className,
  label,
  value,
  tone,
  emphasis = false,
}: {
  className?: string;
  label: string;
  value: string | undefined;
  tone?: BannerTone;
  emphasis?: boolean;
}): JSX.Element {
  return (
    <div className={`min-w-0 px-4 py-3 sm:min-h-24 sm:px-5 sm:py-4 ${className ?? ""}`}>
      <dt className="text-xs font-medium text-foreground-muted">{label}</dt>
      <dd className="mt-2 flex min-w-0 items-center gap-2">
        {value === undefined ? (
          <Skeleton className="h-5 w-28 max-w-full" />
        ) : (
          <>
            {tone ? (
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${statusDotClass(tone)}`}
                aria-hidden="true"
              />
            ) : null}
            <span
              className={
                emphasis
                  ? "min-w-0 break-words font-display text-lg font-semibold tabular-nums text-foreground"
                  : "min-w-0 break-words text-sm font-semibold text-foreground"
              }
            >
              {value}
            </span>
          </>
        )}
      </dd>
    </div>
  );
}

function BillingField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd
        className={`mt-1 break-words text-sm font-medium text-foreground ${mono ? "font-mono text-xs" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}

function OverviewCardSkeleton({ label }: { label: string }): JSX.Element {
  return (
    <div className="space-y-3" role="status">
      <span className="sr-only">{label}</span>
      <Skeleton className="h-8 w-40 max-w-full" />
      <Skeleton className="h-4 w-28 max-w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

function UnavailableCopy({ children }: { children: React.ReactNode }): JSX.Element {
  return <p className="text-sm leading-6 text-foreground-muted">{children}</p>;
}

function OverviewError({
  message,
  retrying,
  onRetry,
  compact = false,
}: {
  message: string;
  retrying: boolean;
  onRetry: () => void;
  compact?: boolean;
}): JSX.Element {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-3 border border-danger/30 bg-danger-subtle px-3 py-2 ${compact ? "rounded-md" : "rounded-lg"}`}
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
  let focusInvoice: Invoice | null = null;

  for (const invoice of invoices) {
    if (invoice.status !== "pending" && invoice.status !== "overdue") continue;

    outstandingCount += 1;
    if (!focusInvoice || comesBefore(invoice, focusInvoice)) focusInvoice = invoice;
  }

  const recentInvoices = [...invoices]
    .sort((left, right) => Date.parse(right.issued_at) - Date.parse(left.issued_at))
    .slice(0, 3);

  return { outstandingCount, focusInvoice, recentInvoices };
}

function buildOverviewBanner(
  subscription: SubscriptionWithPlan | null | undefined,
  summary: InvoiceSummary,
): OverviewBanner {
  if (subscription?.status === "suspended") {
    return {
      tone: "danger",
      eyebrow: "Доступ приостановлен",
      title: "Подписка требует внимания",
      description:
        "Обратитесь в поддержку Aurum Pharma для проверки расчётов и восстановления доступа.",
      invoice: summary.focusInvoice,
    };
  }
  if (subscription?.status === "grace_period") {
    return {
      tone: "warning",
      eyebrow: "Льготный период",
      title: "Срок текущего периода завершён",
      description: summary.focusInvoice
        ? `Проверьте счёт ${summary.focusInvoice.invoice_number} со сроком ${formatBillingDate(summary.focusInvoice.due_at)}.`
        : "Открытый счёт пока не найден. Обратитесь в поддержку для проверки расчётов.",
      invoice: summary.focusInvoice,
    };
  }
  if (summary.focusInvoice?.status === "overdue") {
    return {
      tone: "danger",
      eyebrow: "Нужно действие",
      title: "Оплата просрочена",
      description: `Счёт ${summary.focusInvoice.invoice_number} нужно было оплатить до ${formatBillingDate(summary.focusInvoice.due_at)}.`,
      invoice: summary.focusInvoice,
    };
  }
  if (summary.focusInvoice) {
    return {
      tone: "warning",
      eyebrow: "Следующий шаг",
      title: "Есть счёт, ожидающий оплаты",
      description: `Сумма счёта ${formatBillingMoney(summary.focusInvoice.amount, summary.focusInvoice.currency)}, срок оплаты ${formatBillingDate(summary.focusInvoice.due_at)}.`,
      invoice: summary.focusInvoice,
    };
  }
  if (!subscription) {
    return {
      tone: "neutral",
      eyebrow: "Доступ",
      title: "Подписка не подключена",
      description: "Обратитесь в поддержку Aurum Pharma для подключения тарифа.",
      invoice: null,
    };
  }
  if (subscription.status === "trial") {
    return {
      tone: "info",
      eyebrow: "Пробный период",
      title: "Пробная подписка активна",
      description: `Текущий пробный период действует до ${formatBillingDate(subscription.period_end)}.`,
      invoice: null,
    };
  }
  if (subscription.status === "cancelled" || subscription.status === "archived") {
    return {
      tone: "neutral",
      eyebrow: "Подписка",
      title:
        subscription.status === "archived" ? "Подписка находится в архиве" : "Подписка отменена",
      description: "Обратитесь в поддержку Aurum Pharma, чтобы восстановить обслуживание.",
      invoice: null,
    };
  }
  return {
    tone: "success",
    eyebrow: "Всё в порядке",
    title: "Расчёты актуальны",
    description: `Текущий период действует до ${formatBillingDate(subscription.period_end)}. Открытых счетов нет.`,
    invoice: null,
  };
}

function comesBefore(candidate: Invoice, current: Invoice): boolean {
  if (candidate.status !== current.status) return candidate.status === "overdue";
  return Date.parse(candidate.due_at) < Date.parse(current.due_at);
}

function statusDotClass(tone: BannerTone): string {
  if (tone === "success") return "bg-success";
  if (tone === "warning") return "bg-warning";
  if (tone === "danger") return "bg-danger";
  if (tone === "info") return "bg-info";
  return "bg-foreground-muted";
}

function invoiceCountLabel(count: number): string {
  const absolute = Math.abs(count) % 100;
  const lastDigit = absolute % 10;
  if (absolute > 10 && absolute < 20) return `${count} счетов`;
  if (lastDigit === 1) return `${count} счёт`;
  if (lastDigit >= 2 && lastDigit <= 4) return `${count} счёта`;
  return `${count} счетов`;
}
