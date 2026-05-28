import { Badge, Button, Modal, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import {
  invoiceStatusLabel,
  invoiceStatusTone,
  paymentMethodLabel,
} from "./labels";
import { useInvoiceQuery } from "./queries";

export function InvoiceDetailModal({
  invoiceId,
  onClose,
}: {
  invoiceId: string | null;
  onClose: () => void;
}): JSX.Element {
  const { data, isLoading, error } = useInvoiceQuery(invoiceId);

  return (
    <Modal
      open={invoiceId !== null}
      onClose={onClose}
      title={data ? `Счёт № ${data.invoice_number}` : "Счёт"}
      className="max-w-2xl"
    >
      {isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : error || !data ? (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить счёт")}
        </p>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 text-sm">
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
            <Field
              label="Выставлен"
              value={new Date(data.issued_at).toLocaleDateString("ru-RU")}
            />
            <Field
              label="Срок оплаты"
              value={new Date(data.due_at).toLocaleDateString("ru-RU")}
            />
            {Number(data.discount_amount) > 0 && (
              <Field
                label="Скидка"
                value={`${Number(data.discount_amount).toFixed(2)} ${data.currency}${
                  data.discount_reason ? ` · ${data.discount_reason}` : ""
                }`}
              />
            )}
            {data.paid_at && (
              <Field
                label="Оплачен"
                value={new Date(data.paid_at).toLocaleString("ru-RU")}
              />
            )}
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
                      <TD className="whitespace-nowrap">
                        {new Date(p.paid_at).toLocaleString("ru-RU")}
                      </TD>
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

function Field({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}): JSX.Element {
  return (
    <div>
      <p className="text-xs text-foreground-muted">{label}</p>
      <p>{value}</p>
    </div>
  );
}
