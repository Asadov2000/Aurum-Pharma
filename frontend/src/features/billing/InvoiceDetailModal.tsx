import { Badge, Button, Modal } from "@/components/ui";

import { formatBillingDate, formatBillingMoney } from "./format";
import {
  financialInvoiceStatus,
  financialInvoiceStatusLabel,
  financialInvoiceStatusTone,
} from "./labels";
import { type TenantFinancialInvoice } from "./types";

export function InvoiceDetailModal({
  invoice,
  onClose,
}: {
  invoice: TenantFinancialInvoice | null;
  onClose: () => void;
}): JSX.Element {
  const status = invoice ? financialInvoiceStatus(invoice) : null;
  const paidAmount = invoice
    ? Math.max(Number(invoice.total_amount) - Number(invoice.outstanding_amount), 0)
    : 0;

  return (
    <Modal
      open={invoice !== null}
      onClose={onClose}
      title={invoice ? `Счет № ${invoice.invoice_number}` : "Счет"}
      className="max-w-2xl"
    >
      {invoice && status ? (
        <div className="space-y-5">
          <div className="flex flex-wrap items-end justify-between gap-4 rounded-lg border border-border bg-background px-4 py-3">
            <div>
              <p className="text-xs font-medium text-foreground-muted">Сумма счета</p>
              <p className="mt-1 font-display text-2xl font-semibold tabular-nums text-foreground">
                {formatBillingMoney(invoice.total_amount, invoice.currency)}
              </p>
            </div>
            <Badge tone={financialInvoiceStatusTone[status]} className="mb-1">
              {financialInvoiceStatusLabel[status]}
            </Badge>
          </div>

          <dl className="grid grid-cols-1 gap-x-6 gap-y-4 text-sm sm:grid-cols-2">
            <Field label="Выставлен" value={formatBillingDate(invoice.issued_at)} />
            <Field label="Срок оплаты" value={formatBillingDate(invoice.due_at)} />
            <Field
              label="Расчетный период"
              value={`${formatBillingDate(invoice.period_start)} — ${formatBillingDate(invoice.period_end)}`}
            />
            <Field label="Оплачено" value={formatBillingMoney(paidAmount, invoice.currency)} />
            <Field
              label="Осталось оплатить"
              value={formatBillingMoney(invoice.outstanding_amount, invoice.currency)}
              strong={Number(invoice.outstanding_amount) > 0}
            />
          </dl>

          <p className="rounded-md border border-info/25 bg-info-subtle px-3 py-2 text-xs leading-5 text-info-foreground">
            Остаток обновляется после проверки и подтверждения платежа сотрудником Aurum Pharma.
          </p>

          <div className="flex justify-end border-t border-border pt-3">
            <Button variant="secondary" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

function Field({
  label,
  value,
  strong = false,
}: {
  label: string;
  value: React.ReactNode;
  strong?: boolean;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd
        className={`mt-1 break-words text-foreground ${strong ? "font-semibold tabular-nums" : "font-medium"}`}
      >
        {value}
      </dd>
    </div>
  );
}
