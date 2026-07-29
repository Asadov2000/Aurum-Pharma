import { Badge, Button, Modal, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { invoiceStatusLabel, invoiceStatusTone, paymentMethodLabel } from "./labels";
import { formatBillingDate, formatBillingDateTime } from "./format";
import { useInvoiceQuery } from "./queries";

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
      className="max-w-2xl"
    >
      {isLoading ? (
        <p className="text-sm text-foreground-muted" role="status">
          Загрузка счёта…
        </p>
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
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
            <Field
              label="Статус"
              value={
                <Badge tone={invoiceStatusTone[data.status]}>
                  {invoiceStatusLabel[data.status]}
                </Badge>
              }
            />
            <Field
              label="Сумма"
              value={
                <span className="font-mono text-lg">
                  {Number(data.amount).toFixed(2)} {data.currency}
                </span>
              }
            />
            <Field label="Выставлен" value={formatBillingDate(data.issued_at)} />
            <Field label="Срок оплаты" value={formatBillingDate(data.due_at)} />
            {Number(data.discount_amount) > 0 && (
              <Field
                label="Скидка"
                value={`${Number(data.discount_amount).toFixed(2)} ${data.currency}${
                  data.discount_reason ? ` · ${data.discount_reason}` : ""
                }`}
              />
            )}
            {data.paid_at && <Field label="Оплачен" value={formatBillingDateTime(data.paid_at)} />}
          </div>

          {data.notes && (
            <p className="rounded-md bg-foreground/[0.03] px-3 py-2 text-sm text-foreground-secondary">
              {data.notes}
            </p>
          )}

          <div>
            <p className="mb-2 text-sm font-medium text-foreground-secondary">Платежи</p>
            {data.payments.length === 0 ? (
              <p className="text-sm italic text-foreground-muted">Оплат пока нет</p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Дата</TH>
                    <TH>Способ</TH>
                    <TH>Ссылка</TH>
                    <TH className="text-right">Сумма</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.payments.map((p) => (
                    <TR key={p.id}>
                      <TD className="whitespace-nowrap">{formatBillingDateTime(p.paid_at)}</TD>
                      <TD>{paymentMethodLabel[p.method]}</TD>
                      <TD className="font-mono text-xs">{p.reference ?? "—"}</TD>
                      <TD className="text-right font-mono">
                        {Number(p.amount).toFixed(2)} {p.currency}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </div>

          <div className="flex justify-end">
            <Button variant="ghost" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }): JSX.Element {
  return (
    <div>
      <p className="text-xs text-foreground-muted">{label}</p>
      <p>{value}</p>
    </div>
  );
}
