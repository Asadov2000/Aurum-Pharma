import { useState } from "react";
import { Button, Modal, SkeletonRows } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";
import { useCashReceipt } from "./cashQueries";
import { printCashReceipt } from "./cashReceiptPrint";
import { formatBillingDateTime, formatBillingMoney } from "./format";

export function CashReceiptModal({
  id,
  tenantId,
  onClose,
}: {
  id: string | null;
  tenantId?: string;
  onClose: () => void;
}): JSX.Element {
  const query = useCashReceipt(id, tenantId);
  const [printError, setPrintError] = useState<string | null>(null);
  const [printing, setPrinting] = useState(false);
  const receipt = query.data;
  const print = async () => {
    if (!receipt || query.error || query.isFetching || printing) return;
    setPrintError(null);
    setPrinting(true);
    try {
      await printCashReceipt(receipt);
    } catch {
      setPrintError("Не удалось открыть печать. Повторите попытку.");
    } finally {
      setPrinting(false);
    }
  };
  return (
    <Modal open={id !== null} onClose={onClose} title="Подтверждение приёма наличных">
      {query.isLoading ? (
        <SkeletonRows rows={6} />
      ) : query.error ? (
        <div role="alert" className="space-y-3 text-danger-foreground">
          <p>{describeApiError(query.error, "Не удалось загрузить подтверждение")}</p>
          <Button
            variant="secondary"
            isLoading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : receipt ? (
        <div className="space-y-4">
          <p className="text-sm text-foreground-muted">Документ № {receipt.document_number}</p>
          <p className="text-2xl font-semibold">
            {formatBillingMoney(receipt.amount, receipt.currency)}
          </p>
          <dl className="grid gap-3 text-sm">
            <ReceiptField label="Клиент" value={receipt.tenant_name} />
            <ReceiptField label="Назначение" value={receipt.purpose} />
            <ReceiptField
              label="Получено наличными"
              value={formatBillingDateTime(receipt.paid_at)}
            />
            <ReceiptField
              label="Подтверждено"
              value={formatBillingDateTime(receipt.confirmed_at)}
            />
            <ReceiptField
              label="Зачтено в счета"
              value={formatBillingMoney(receipt.allocated_amount, receipt.currency)}
            />
            <ReceiptField
              label="Аванс"
              value={formatBillingMoney(receipt.credit_amount, receipt.currency)}
            />
          </dl>
          {receipt.allocations.map((allocation) => (
            <p key={allocation.invoice_id} className="text-sm">
              Счёт {allocation.invoice_number}:{" "}
              {formatBillingMoney(allocation.amount, receipt.currency)}
            </p>
          ))}
          <p className="text-xs text-foreground-muted">
            Документ подтверждает расчёты с Aurum Pharma. Не является фискальным чеком.
          </p>
          {printError ? (
            <p role="alert" className="text-sm text-danger-foreground">
              {printError}
            </p>
          ) : null}
        </div>
      ) : null}
      <div className="mt-5 flex flex-wrap justify-end gap-2 border-t border-border pt-4">
        <Button variant="secondary" onClick={onClose}>
          Закрыть
        </Button>
        <Button
          disabled={!receipt || Boolean(query.error) || query.isFetching}
          isLoading={printing}
          onClick={() => void print()}
        >
          Печать / сохранить PDF
        </Button>
      </div>
    </Modal>
  );
}

function ReceiptField({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="grid gap-1 sm:grid-cols-[10rem_1fr]">
      <dt className="text-foreground-muted">{label}</dt>
      <dd className="break-words font-medium">{value}</dd>
    </div>
  );
}
