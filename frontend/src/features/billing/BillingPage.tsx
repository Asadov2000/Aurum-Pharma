import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  PageHeader,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { formatBillingDate } from "./format";
import { InvoiceDetailModal } from "./InvoiceDetailModal";
import {
  billingPeriodLabel,
  invoiceStatusLabel,
  invoiceStatusTone,
  subscriptionStatusLabel,
  subscriptionStatusTone,
} from "./labels";
import { useInvoicesQuery, useSubscriptionQuery } from "./queries";

export function BillingPage(): JSX.Element {
  const subscription = useSubscriptionQuery();
  const invoices = useInvoicesQuery();
  const [openInvoiceId, setOpenInvoiceId] = useState<string | null>(null);
  const subscriptionData = subscription.data;
  const invoiceItems = invoices.data ?? [];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Биллинг"
        description="Подписка, стоимость обслуживания и история счетов аптеки."
      />

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Текущая подписка</CardTitle>
          {subscriptionData && (
            <Badge tone={subscriptionStatusTone[subscriptionData.status]}>
              {subscriptionStatusLabel[subscriptionData.status]}
            </Badge>
          )}
        </CardHeader>
        <CardContent>
          {subscription.isLoading ? (
            <p className="text-sm text-foreground-muted" role="status">
              Загрузка подписки…
            </p>
          ) : subscription.error ? (
            <BillingError
              message={describeApiError(subscription.error, "Не удалось загрузить подписку")}
              retrying={subscription.isFetching}
              onRetry={() => void subscription.refetch()}
            />
          ) : !subscriptionData ? (
            <p className="text-sm italic text-foreground-muted">
              Подписки пока нет. Свяжитесь с поддержкой, чтобы её активировать.
            </p>
          ) : (
            <div>
              <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase text-foreground-muted">План</p>
                  <p className="mt-1 break-words text-xl font-semibold text-foreground">
                    {subscriptionData.plan_name}
                  </p>
                  <p className="mt-0.5 font-mono text-xs text-foreground-muted">
                    {subscriptionData.plan_code}
                  </p>
                </div>
                <div className="lg:text-right">
                  <p className="text-xs font-medium text-foreground-muted">За период</p>
                  <p className="mt-1 font-mono text-3xl font-semibold tabular-nums text-foreground">
                    {Number(subscriptionData.amount).toFixed(2)}
                    <span className="ml-2 text-base font-medium text-foreground-muted">
                      {subscriptionData.currency}
                    </span>
                  </p>
                </div>
              </div>

              <dl className="mt-5 grid grid-cols-1 gap-x-6 gap-y-4 border-t border-border pt-4 sm:grid-cols-3">
                <BillingField
                  label="Период оплаты"
                  value={billingPeriodLabel[subscriptionData.billing_period]}
                />
                <BillingField label="Точек в подписке" value={subscriptionData.branches_count} />
                <BillingField
                  label="Действует до"
                  value={formatBillingDate(subscriptionData.period_end)}
                />
              </dl>
            </div>
          )}
        </CardContent>
      </Card>

      <section className="space-y-3" aria-labelledby="billing-invoices-heading">
        <div className="flex items-center justify-between gap-3">
          <h2 id="billing-invoices-heading" className="text-base font-semibold text-foreground">
            История счетов
          </h2>
          {!invoices.isLoading && !invoices.error && (
            <Badge tone="neutral">{invoiceItems.length}</Badge>
          )}
        </div>

        {invoices.isLoading ? (
          <p className="text-sm text-foreground-muted" role="status">
            Загрузка счетов…
          </p>
        ) : invoices.error ? (
          <BillingError
            message={describeApiError(invoices.error, "Не удалось загрузить счета")}
            retrying={invoices.isFetching}
            onRetry={() => void invoices.refetch()}
          />
        ) : invoiceItems.length === 0 ? (
          <TableEmpty>Счетов пока нет</TableEmpty>
        ) : (
          <>
            <div className="hidden sm:block">
              <Table>
                <THead>
                  <TR>
                    <TH>Номер</TH>
                    <TH>Выставлен</TH>
                    <TH>Срок</TH>
                    <TH className="text-right">Сумма</TH>
                    <TH>Статус</TH>
                  </TR>
                </THead>
                <TBody>
                  {invoiceItems.map((inv) => (
                    <TR key={inv.id}>
                      <TD>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="-ml-2 h-auto px-2 py-1 font-mono text-xs text-primary"
                          onClick={() => setOpenInvoiceId(inv.id)}
                          aria-label={`Открыть счёт ${inv.invoice_number}`}
                        >
                          {inv.invoice_number}
                        </Button>
                      </TD>
                      <TD>{formatBillingDate(inv.issued_at)}</TD>
                      <TD>{formatBillingDate(inv.due_at)}</TD>
                      <TD className="text-right font-mono">
                        {Number(inv.amount).toFixed(2)} {inv.currency}
                      </TD>
                      <TD>
                        <Badge tone={invoiceStatusTone[inv.status]}>
                          {invoiceStatusLabel[inv.status]}
                        </Badge>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>

            <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface sm:hidden">
              {invoiceItems.map((inv) => (
                <li key={inv.id}>
                  <button
                    type="button"
                    className="block w-full px-4 py-3 text-left transition-colors duration-fast hover:bg-foreground/[0.025] active:bg-foreground/5"
                    onClick={() => setOpenInvoiceId(inv.id)}
                    aria-label={`Открыть счёт ${inv.invoice_number}`}
                  >
                    <span className="flex items-start justify-between gap-3">
                      <span className="min-w-0">
                        <span className="block truncate font-mono text-sm font-semibold text-foreground">
                          {inv.invoice_number}
                        </span>
                        <span className="mt-1 block text-xs text-foreground-muted">
                          Срок: {formatBillingDate(inv.due_at)}
                        </span>
                      </span>
                      <Badge tone={invoiceStatusTone[inv.status]}>
                        {invoiceStatusLabel[inv.status]}
                      </Badge>
                    </span>
                    <span className="mt-3 block font-mono text-lg font-semibold tabular-nums text-foreground">
                      {Number(inv.amount).toFixed(2)} {inv.currency}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <InvoiceDetailModal invoiceId={openInvoiceId} onClose={() => setOpenInvoiceId(null)} />
    </div>
  );
}

function BillingField({ label, value }: { label: string; value: React.ReactNode }): JSX.Element {
  return (
    <div>
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className="mt-1 text-sm font-medium text-foreground">{value}</dd>
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
