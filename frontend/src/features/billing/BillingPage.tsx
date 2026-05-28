import { useState } from "react";

import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

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

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-foreground">Биллинг</h1>

      {/* SUBSCRIPTION CARD */}
      <Card>
        <CardHeader>
          <CardTitle>Текущая подписка</CardTitle>
        </CardHeader>
        <CardContent>
          {subscription.isLoading ? (
            <p className="text-sm text-foreground-muted">Загрузка…</p>
          ) : subscription.error ? (
            <p className="text-sm text-danger">
              {describeApiError(subscription.error, "Не удалось загрузить подписку")}
            </p>
          ) : !subscription.data ? (
            <p className="text-sm italic text-foreground-muted">
              Подписки пока нет. Свяжитесь с поддержкой, чтобы её активировать.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <div>
                <p className="text-xs text-foreground-muted">План</p>
                <p className="text-lg font-medium">{subscription.data.plan_name}</p>
                <p className="font-mono text-xs text-foreground-muted">
                  {subscription.data.plan_code}
                </p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Статус</p>
                <Badge tone={subscriptionStatusTone[subscription.data.status]}>
                  {subscriptionStatusLabel[subscription.data.status]}
                </Badge>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Период</p>
                <p>{billingPeriodLabel[subscription.data.billing_period]}</p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Точек</p>
                <p>{subscription.data.branches_count}</p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Сумма за период</p>
                <p className="font-mono">
                  {Number(subscription.data.amount).toFixed(2)} {subscription.data.currency}
                </p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Действует до</p>
                <p>{new Date(subscription.data.period_end).toLocaleDateString("ru-RU")}</p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* INVOICES */}
      <Card>
        <CardHeader>
          <CardTitle>Счета</CardTitle>
        </CardHeader>
        <CardContent>
          {invoices.isLoading ? (
            <p className="text-sm text-foreground-muted">Загрузка…</p>
          ) : invoices.error ? (
            <p className="text-sm text-danger">
              {describeApiError(invoices.error, "Не удалось загрузить счета")}
            </p>
          ) : !invoices.data || invoices.data.length === 0 ? (
            <TableEmpty>Счетов пока нет</TableEmpty>
          ) : (
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
                {invoices.data.map((inv) => (
                  <TR
                    key={inv.id}
                    className="cursor-pointer"
                    onClick={() => setOpenInvoiceId(inv.id)}
                  >
                    <TD className="font-mono text-xs">{inv.invoice_number}</TD>
                    <TD>{new Date(inv.issued_at).toLocaleDateString("ru-RU")}</TD>
                    <TD>{new Date(inv.due_at).toLocaleDateString("ru-RU")}</TD>
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
          )}
        </CardContent>
      </Card>

      <InvoiceDetailModal
        invoiceId={openInvoiceId}
        onClose={() => setOpenInvoiceId(null)}
      />
    </div>
  );
}
