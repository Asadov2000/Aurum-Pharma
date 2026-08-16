import { useMemo } from "react";

import { Badge, Button, Card } from "@/components/ui";

import { formatBillingDate, formatBillingMoney } from "./format";
import {
  billingPeriodLabel,
  financialInvoiceStatus,
  financialInvoiceStatusLabel,
  financialInvoiceStatusTone,
  subscriptionStatusLabel,
  subscriptionStatusTone,
} from "./labels";
import { type TenantFinancialAccount, type TenantFinancialInvoice } from "./types";

interface BillingOverviewProps {
  account: TenantFinancialAccount;
  fetching: boolean;
  onRefresh: () => void;
  onOpenInvoice: (invoiceId: string) => void;
  onShowHistory: () => void;
}

type BannerTone = "neutral" | "success" | "warning" | "danger" | "info";

interface OverviewBanner {
  tone: BannerTone;
  eyebrow: string;
  title: string;
  description: string;
  invoice: TenantFinancialInvoice | null;
}

const bannerClasses: Record<BannerTone, string> = {
  neutral: "border-border bg-surface text-foreground",
  success: "border-success/30 bg-success-subtle text-success-foreground",
  warning: "border-warning/35 bg-warning-subtle text-warning-foreground",
  danger: "border-danger/35 bg-danger-subtle text-danger-foreground",
  info: "border-info/35 bg-info-subtle text-info-foreground",
};

export function BillingOverview({
  account,
  fetching,
  onRefresh,
  onOpenInvoice,
  onShowHistory,
}: BillingOverviewProps): JSX.Element {
  const focusInvoice = useMemo(() => findFocusInvoice(account.invoices), [account.invoices]);
  const banner = buildOverviewBanner(account, focusInvoice);
  const subscription = account.subscription;

  return (
    <section className="space-y-4" aria-label="Сводка по тарифу и оплате">
      <Card aria-label="Ключевые показатели расчетов">
        <dl className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5">
          <SummaryMetric
            label="Подписка"
            value={subscription ? subscriptionStatusLabel[subscription.status] : "Не подключена"}
            tone={subscription ? subscriptionStatusTone[subscription.status] : "neutral"}
            className="border-b border-r border-border sm:border-b xl:border-b-0"
          />
          <SummaryMetric
            label="Текущий период до"
            value={subscription ? formatBillingDate(subscription.period_end) : "—"}
            className="border-b border-border sm:border-r xl:border-b-0"
          />
          <SummaryMetric
            label="Стоимость периода"
            value={
              subscription ? formatBillingMoney(subscription.amount, subscription.currency) : "—"
            }
            className="border-b border-r border-border sm:border-r-0 xl:border-b-0 xl:border-r"
          />
          <SummaryMetric
            label="К оплате"
            value={formatBillingMoney(account.outstanding_amount, account.currency)}
            tone={Number(account.outstanding_amount) > 0 ? "warning" : "success"}
            className="border-b border-border sm:border-b-0 sm:border-r"
          />
          <SummaryMetric
            label="Аванс"
            value={formatBillingMoney(account.credit_balance, account.currency)}
            tone={Number(account.credit_balance) > 0 ? "info" : "neutral"}
            className="col-span-2 sm:col-span-1"
          />
        </dl>
      </Card>

      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(18rem,0.75fr)]">
        <div
          className={`min-w-0 rounded-lg border px-4 py-4 sm:px-5 ${bannerClasses[banner.tone]}`}
          role="status"
        >
          <div className="flex min-w-0 flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase text-current/75">{banner.eyebrow}</p>
              <h2 className="mt-1 text-lg font-semibold text-current">{banner.title}</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-current/85">
                {banner.description}
              </p>
            </div>
            {banner.invoice ? (
              <Badge tone={financialInvoiceStatusTone[financialInvoiceStatus(banner.invoice)]}>
                {financialInvoiceStatusLabel[financialInvoiceStatus(banner.invoice)]}
              </Badge>
            ) : null}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {banner.invoice ? (
              <Button
                size="sm"
                onClick={() => onOpenInvoice(banner.invoice!.invoice_id)}
                aria-label={`Открыть счет ${banner.invoice.invoice_number}`}
              >
                Открыть счет
              </Button>
            ) : null}
            <Button variant="secondary" size="sm" onClick={onShowHistory}>
              История счетов
            </Button>
            <Button variant="ghost" size="sm" isLoading={fetching} onClick={onRefresh}>
              Обновить
            </Button>
          </div>
        </div>

        <Card className="min-w-0 p-4" aria-label="Параметры тарифа">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-medium text-foreground-muted">Тариф</p>
              <h2 className="mt-1 break-words text-base font-semibold text-foreground">
                {subscription?.plan_name ?? "Не подключен"}
              </h2>
            </div>
            {subscription ? (
              <Badge tone={subscriptionStatusTone[subscription.status]}>
                {subscriptionStatusLabel[subscription.status]}
              </Badge>
            ) : null}
          </div>
          {subscription ? (
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border pt-4">
              <BillingField
                label="Расчет"
                value={billingPeriodLabel[subscription.billing_period]}
              />
              <BillingField label="Точек" value={subscription.branches_count} />
              <BillingField label="Начало" value={formatBillingDate(subscription.period_start)} />
              <BillingField label="Окончание" value={formatBillingDate(subscription.period_end)} />
            </dl>
          ) : (
            <p className="mt-4 border-t border-border pt-4 text-sm leading-6 text-foreground-muted">
              Для подключения тарифа обратитесь в поддержку Aurum Pharma.
            </p>
          )}
        </Card>
      </div>
    </section>
  );
}

function findFocusInvoice(
  invoices: readonly TenantFinancialInvoice[],
): TenantFinancialInvoice | null {
  const actionable = invoices.filter(
    (invoice) =>
      invoice.document_state === "issued" &&
      invoice.settlement_state !== "paid" &&
      invoice.settlement_state !== "written_off" &&
      Number(invoice.outstanding_amount) > 0,
  );
  actionable.sort((left, right) => {
    const leftOverdue = left.collection_state === "overdue" ? 0 : 1;
    const rightOverdue = right.collection_state === "overdue" ? 0 : 1;
    return leftOverdue - rightOverdue || Date.parse(left.due_at) - Date.parse(right.due_at);
  });
  return actionable[0] ?? null;
}

function buildOverviewBanner(
  account: TenantFinancialAccount,
  invoice: TenantFinancialInvoice | null,
): OverviewBanner {
  const subscription = account.subscription;
  if (subscription?.status === "suspended") {
    return {
      tone: "danger",
      eyebrow: "Доступ приостановлен",
      title: "Подписка требует внимания",
      description:
        "Проверьте открытые счета и свяжитесь с поддержкой Aurum Pharma для восстановления обслуживания.",
      invoice,
    };
  }
  if (invoice?.collection_state === "overdue") {
    return {
      tone: "danger",
      eyebrow: "Нужно действие",
      title: "Оплата просрочена",
      description: `По счету ${invoice.invoice_number} осталось оплатить ${formatBillingMoney(invoice.outstanding_amount, invoice.currency)}. Срок был до ${formatBillingDate(invoice.due_at)}.`,
      invoice,
    };
  }
  if (invoice) {
    const partial = invoice.settlement_state === "partially_paid";
    return {
      tone: "warning",
      eyebrow: partial ? "Частичная оплата" : "Следующий шаг",
      title: partial ? "По счету остался непогашенный остаток" : "Есть счет, ожидающий оплаты",
      description: `К оплате ${formatBillingMoney(invoice.outstanding_amount, invoice.currency)}, срок до ${formatBillingDate(invoice.due_at)}.`,
      invoice,
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
      description: `Пробный период действует до ${formatBillingDate(subscription.period_end)}. Открытых счетов нет.`,
      invoice: null,
    };
  }
  return {
    tone: "success",
    eyebrow: "Все в порядке",
    title: "Расчеты актуальны",
    description: `Текущий период действует до ${formatBillingDate(subscription.period_end)}. Открытых счетов нет.`,
    invoice: null,
  };
}

function SummaryMetric({
  label,
  value,
  tone = "neutral",
  className = "",
}: {
  label: string;
  value: string;
  tone?: BannerTone;
  className?: string;
}): JSX.Element {
  const toneClass =
    tone === "danger"
      ? "text-danger-foreground"
      : tone === "warning"
        ? "text-warning-foreground"
        : tone === "success"
          ? "text-success-foreground"
          : tone === "info"
            ? "text-info-foreground"
            : "text-foreground";
  return (
    <div className={`min-w-0 px-4 py-3 ${className}`}>
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className={`mt-2 break-words text-base font-semibold tabular-nums ${toneClass}`}>
        {value}
      </dd>
    </div>
  );
}

function BillingField({ label, value }: { label: string; value: React.ReactNode }): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-medium text-foreground">{value}</dd>
    </div>
  );
}
