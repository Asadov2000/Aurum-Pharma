import { useEffect, useState } from "react";

import { Badge, Button, Modal, Table, TBody, TD, TH, THead, TR } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { paymentMethodLabel } from "@/features/pos/labels";
import { ReceiptPrintModal } from "@/features/pos/ReceiptPrintModal";
import { type PaymentMethodRead } from "@/features/pos/types";
import { describeApiError } from "@/lib/errorMessages";

import { useSaleDetailsQuery } from "./queries";
import { RefundModal } from "./RefundModal";
import { type SaleListItem } from "./types";

export function SaleDetailModal({
  row,
  onClose,
}: {
  row: SaleListItem;
  onClose: () => void;
}): JSX.Element {
  const { user } = useAuth();
  const canRefund = hasPermission(user, "pos.refund");
  // currentId can swap to a freshly-created return sale (after refund) or to
  // the parent sale (when viewing a return), so navigation stays in-modal.
  const [currentId, setCurrentId] = useState(row.id);
  const [refundOpen, setRefundOpen] = useState(false);
  const [printOpen, setPrintOpen] = useState(false);
  const details = useSaleDetailsQuery(currentId);
  const { data, isLoading, error } = details;

  // If we navigated to a different sale than the row we opened from, the
  // row-level flags (has_refund) no longer apply.
  const isOriginalRow = currentId === row.id;

  useEffect(() => {
    setCurrentId(row.id);
  }, [row.id]);

  const isRefund = data ? data.sale_type === "return" : row.is_refund;
  const hasRefundableItems =
    data?.items.some((item) => Number(item.qty) - Number(item.refunded_qty ?? "0") > 0.0005) ??
    false;
  const hasRefundedItems =
    data?.items.some((item) => Number(item.refunded_qty ?? "0") > 0.0005) ?? false;

  return (
    <Modal
      open
      onClose={onClose}
      title={`Чек № ${data?.receipt_number ?? row.receipt_number ?? "—"}`}
      className="max-w-4xl"
    >
      {error ? (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm text-danger-foreground"
        >
          <span>{describeApiError(error, "Не удалось загрузить чек. Проверьте соединение.")}</span>
          <Button
            variant="secondary"
            size="sm"
            isLoading={details.isFetching}
            onClick={() => void details.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : isLoading || !data ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {isRefund ? (
              <Badge tone="warning">Чек возврата</Badge>
            ) : data.status === "voided" ? (
              <Badge tone="danger">Возвращено полностью</Badge>
            ) : hasRefundedItems ? (
              <Badge tone="warning">Возвращено частично</Badge>
            ) : (
              <Badge tone="success">Продажа завершена</Badge>
            )}
          </div>

          <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Дата и время" value={fmtDate(data.completed_at)} />
            <Field label="Сумма" value={`${formatMoney(data.total_amount)} ${data.currency}`} />
            {isOriginalRow ? (
              <>
                <Field label="Кассир" value={row.cashier_name ?? "Не указан"} />
                <Field label="Торговая точка" value={row.branch_name ?? "Не указана"} />
                <Field label="Рабочая касса" value={row.register_name ?? "Не указана"} />
              </>
            ) : null}
            {data.parent_sale_id && (
              <div className="sm:col-span-2 lg:col-span-3">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setCurrentId(data.parent_sale_id as string)}
                >
                  ← Открыть исходную продажу
                </Button>
              </div>
            )}
          </div>

          <div>
            <p className="mb-1 text-xs font-medium text-foreground-muted">Товары</p>
            <Table>
              <THead>
                <TR>
                  <TH>Товар</TH>
                  <TH className="text-right">Кол-во</TH>
                  {!isRefund && <TH className="text-right">Возвращено</TH>}
                  <TH className="text-right">Цена</TH>
                  <TH className="text-right">Сумма</TH>
                </TR>
              </THead>
              <TBody>
                {data.items.map((it) => (
                  <TR key={it.id}>
                    <TD>
                      <p className="font-medium">{it.name ?? `Товар, строка ${it.position}`}</p>
                      <p className="text-xs text-foreground-muted">
                        Партия {it.batch_number ?? "без номера"}
                      </p>
                    </TD>
                    <TD className="text-right font-mono">{formatQty(it.qty)}</TD>
                    {!isRefund && (
                      <TD className="text-right font-mono">{formatQty(it.refunded_qty ?? "0")}</TD>
                    )}
                    <TD className="text-right font-mono">{formatMoney(it.unit_price)}</TD>
                    <TD className="text-right font-mono">{formatMoney(it.total_price)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>

          <div>
            <p className="mb-1 text-xs font-medium text-foreground-muted">Оплаты</p>
            {data.payments.length === 0 ? (
              <p className="text-sm text-foreground-muted">Оплата не указана</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {data.payments.map((p) => (
                  <li key={p.id} className="flex flex-wrap justify-between gap-2">
                    <span>
                      {paymentMethodLabel[p.payment_method as PaymentMethodRead] ??
                        p.payment_method}
                    </span>
                    <span className="font-mono">
                      {formatMoney(p.amount)} {p.currency}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="secondary" onClick={() => setPrintOpen(true)}>
              {isRefund ? "Печать возврата" : "Печать чека"}
            </Button>
            {/* Partial refunds remain available until every line is returned. */}
            {canRefund &&
              data.sale_type === "sale" &&
              data.status === "completed" &&
              hasRefundableItems && (
                <Button onClick={() => setRefundOpen(true)}>Оформить возврат</Button>
              )}
            <Button variant="ghost" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      )}

      {canRefund && refundOpen && data && (
        <RefundModal
          sale={data}
          onClose={() => setRefundOpen(false)}
          onRefunded={(returnSaleId) => {
            setRefundOpen(false);
            setCurrentId(returnSaleId); // show the fresh return receipt
          }}
        />
      )}

      {printOpen && data && (
        <ReceiptPrintModal
          saleId={currentId}
          registerId={data.register_id}
          onClose={() => setPrintOpen(false)}
        />
      )}
    </Modal>
  );
}

function Field({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div>
      <p className="text-xs text-foreground-muted">{label}</p>
      <p>{value}</p>
    </div>
  );
}

function fmtDate(v: string | null): string {
  if (!v) return "—";
  const date = new Date(v);
  return Number.isNaN(date.getTime()) ? v : date.toLocaleString("ru-RU");
}

function formatMoney(value: string): string {
  return Number(value).toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatQty(value: string): string {
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}
