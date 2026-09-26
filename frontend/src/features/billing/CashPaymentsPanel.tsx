import { useState } from "react";
import { Button, Pagination, SkeletonRows, TableEmpty } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";
import { CashReceiptModal } from "./CashReceiptModal";
import { CASH_PAGE_SIZE, useTenantCashPayments } from "./cashQueries";
import { formatBillingDateTime, formatBillingMoney } from "./format";

export function CashPaymentsPanel(): JSX.Element {
  const [page, setPage] = useState(1);
  const [receiptId, setReceiptId] = useState<string | null>(null);
  const query = useTenantCashPayments(page);
  return (
    <section className="space-y-3" aria-labelledby="cash-payments-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="cash-payments-heading" className="font-semibold">
            Наличные оплаты и документы
          </h2>
          <p className="mt-1 text-sm text-foreground-muted">
            Оплаты, подтверждённые сотрудниками Aurum Pharma.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          isLoading={query.isFetching}
          onClick={() => void query.refetch()}
        >
          Обновить документы
        </Button>
      </div>
      {query.isLoading ? (
        <SkeletonRows rows={3} />
      ) : query.error ? (
        <p role="alert" className="text-sm text-danger-foreground">
          {describeApiError(query.error, "Не удалось загрузить наличные оплаты")}
        </p>
      ) : !query.data?.items.length ? (
        <TableEmpty title="Подтверждённых наличных оплат пока нет">
          После передачи денег сотрудник Aurum зарегистрирует оплату. Документ появится после
          независимого подтверждения.
        </TableEmpty>
      ) : (
        <>
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
            {query.data.items.map((payment) => (
              <li
                key={payment.id}
                className="flex flex-wrap items-center justify-between gap-3 p-4"
              >
                <div className="min-w-0">
                  <p className="font-semibold">
                    {formatBillingMoney(payment.amount, payment.currency)}
                  </p>
                  <p className="mt-1 break-words text-sm">{payment.purpose}</p>
                  <p className="mt-1 text-xs text-foreground-muted">
                    {payment.document_number} · {formatBillingDateTime(payment.paid_at)}
                  </p>
                </div>
                <Button size="sm" variant="secondary" onClick={() => setReceiptId(payment.id)}>
                  Открыть документ
                </Button>
              </li>
            ))}
          </ul>
          {query.data.total > CASH_PAGE_SIZE ? (
            <Pagination
              page={page}
              pageSize={CASH_PAGE_SIZE}
              total={query.data.total}
              onPage={setPage}
            />
          ) : null}
        </>
      )}
      {receiptId ? <CashReceiptModal id={receiptId} onClose={() => setReceiptId(null)} /> : null}
    </section>
  );
}
