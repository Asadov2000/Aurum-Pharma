import { Badge, Button, Modal, Skeleton, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { formatBillingDate, formatBillingDateTime, formatBillingMoney } from "./format";
import { invoiceStatusLabel, invoiceStatusTone, paymentMethodLabel } from "./labels";
import { useInvoiceQuery } from "./queries";
import { type Payment } from "./types";

export function InvoiceDetailModal({
  invoiceId,
  onClose,
}: {
  invoiceId: string | null;
  onClose: () => void;
}): JSX.Element {
  const { data, isLoading, isFetching, error, refetch } = useInvoiceQuery(invoiceId);

  return (
    <Modal
      open={invoiceId !== null}
      onClose={onClose}
      title={data ? `Счёт № ${data.invoice_number}` : "Счёт"}
      className="max-w-3xl"
    >
      {isLoading ? (
        <div className="space-y-4" role="status">
          <span className="sr-only">Загрузка счёта…</span>
          <Skeleton className="h-24 w-full" />
          <div className="grid grid-cols-2 gap-3">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        </div>
      ) : error || !data ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2"
          role="alert"
        >
          <p className="text-sm text-danger-foreground">
            {describeApiError(error, "Не удалось загрузить счёт")}
          </p>
          <Button
            variant="secondary"
            size="sm"
            isLoading={isFetching}
            onClick={() => void refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-end justify-between gap-4 rounded-lg border border-border bg-background px-4 py-3">
            <div>
              <p className="text-xs font-medium text-foreground-muted">Сумма счёта</p>
              <p className="mt-1 font-display text-2xl font-semibold tabular-nums text-foreground">
                {formatBillingMoney(data.amount, data.currency)}
              </p>
            </div>
            <Badge tone={invoiceStatusTone[data.status]} className="mb-1">
              {invoiceStatusLabel[data.status]}
            </Badge>
          </div>

          <dl className="grid grid-cols-1 gap-x-6 gap-y-4 text-sm sm:grid-cols-2">
            <Field label="Выставлен" value={formatBillingDate(data.issued_at)} />
            <Field label="Срок оплаты" value={formatBillingDate(data.due_at)} />
            {Number(data.discount_amount) > 0 ? (
              <Field
                label="Скидка"
                value={formatBillingMoney(data.discount_amount, data.currency)}
                detail={data.discount_reason}
              />
            ) : null}
            {data.paid_at ? (
              <Field label="Оплачен" value={formatBillingDateTime(data.paid_at)} />
            ) : null}
          </dl>

          {data.notes ? (
            <div className="border-l-2 border-primary/45 pl-3">
              <p className="text-xs font-medium text-foreground-muted">Примечание</p>
              <p className="mt-1 text-sm leading-6 text-foreground-secondary">{data.notes}</p>
            </div>
          ) : null}

          <section aria-labelledby="invoice-payments-heading">
            <div className="mb-2 flex items-center justify-between gap-3">
              <h3 id="invoice-payments-heading" className="text-sm font-semibold text-foreground">
                Платежи
              </h3>
              <Badge tone="neutral">{data.payments.length}</Badge>
            </div>
            {data.payments.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-foreground-muted">
                Зарегистрированных платежей пока нет
              </p>
            ) : (
              <PaymentHistory payments={data.payments} />
            )}
          </section>

          <p className="rounded-md border border-info/25 bg-info-subtle px-3 py-2 text-xs leading-5 text-info-foreground">
            Платежи отображаются после регистрации администрацией Aurum Pharma.
          </p>

          <div className="flex justify-end border-t border-border pt-3">
            <Button variant="secondary" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function PaymentHistory({ payments }: { payments: readonly Payment[] }): JSX.Element {
  return (
    <>
      <div className="hidden sm:block">
        <Table>
          <THead>
            <TR>
              <TH>Дата</TH>
              <TH>Способ</TH>
              <TH>Реквизиты</TH>
              <TH className="text-right">Сумма</TH>
            </TR>
          </THead>
          <TBody>
            {payments.map((payment) => (
              <TR key={payment.id}>
                <TD className="whitespace-nowrap">{formatBillingDateTime(payment.paid_at)}</TD>
                <TD>{paymentMethodLabel[payment.method]}</TD>
                <TD className="max-w-52 break-all font-mono text-xs">{payment.reference ?? "—"}</TD>
                <TD className="whitespace-nowrap text-right tabular-nums">
                  {formatBillingMoney(payment.amount, payment.currency)}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </div>

      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface sm:hidden">
        {payments.map((payment) => (
          <li key={payment.id} className="space-y-2 px-3 py-3 text-sm">
            <div className="flex items-start justify-between gap-3">
              <span className="text-foreground-secondary">
                {paymentMethodLabel[payment.method]}
              </span>
              <span className="whitespace-nowrap font-semibold tabular-nums text-foreground">
                {formatBillingMoney(payment.amount, payment.currency)}
              </span>
            </div>
            <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs text-foreground-muted">
              <span>{formatBillingDateTime(payment.paid_at)}</span>
              <span className="break-all font-mono">{payment.reference ?? "Без реквизитов"}</span>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function Field({
  label,
  value,
  detail,
}: {
  label: string;
  value: React.ReactNode;
  detail?: string | null;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className="mt-1 break-words font-medium text-foreground">{value}</dd>
      {detail ? <dd className="mt-0.5 text-xs text-foreground-muted">{detail}</dd> : null}
    </div>
  );
}
