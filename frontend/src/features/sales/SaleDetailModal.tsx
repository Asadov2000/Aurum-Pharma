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
  const { data, isLoading, error } = useSaleDetailsQuery(currentId);

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

  return (
    <Modal
      open
      onClose={onClose}
      title={`Чек № ${data?.receipt_number ?? row.receipt_number ?? "—"}`}
      className="max-w-2xl"
    >
      {error && (
        <p className="text-sm text-danger">{describeApiError(error, "Не удалось загрузить чек")}</p>
      )}
      {isLoading || !data ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {isRefund ? (
              <Badge tone="warning">Возврат</Badge>
            ) : (
              <Badge tone="success">Продажа</Badge>
            )}
            {data.status === "voided" && <Badge tone="danger">Отменён возвратом</Badge>}
            {isOriginalRow && row.has_refund && !isRefund && (
              <Badge tone="info">
                Есть возврат
                {row.refund_receipt_number ? ` № ${row.refund_receipt_number}` : ""}
              </Badge>
            )}
          </div>

          <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
            <Field label="Дата" value={fmtDate(data.completed_at)} />
            <Field
              label="Сумма"
              value={`${Number(data.total_amount).toFixed(2)} ${data.currency}`}
            />
            {data.parent_sale_id && (
              <div className="sm:col-span-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setCurrentId(data.parent_sale_id as string)}
                >
                  ← Открыть оригинальный чек
                </Button>
              </div>
            )}
          </div>

          <div>
            <p className="mb-1 text-xs font-medium text-foreground-muted">Позиции</p>
            <Table>
              <THead>
                <TR>
                  <TH>#</TH>
                  <TH className="text-right">Кол-во</TH>
                  <TH className="text-right">Цена</TH>
                  <TH className="text-right">Сумма</TH>
                </TR>
              </THead>
              <TBody>
                {data.items.map((it) => (
                  <TR key={it.id}>
                    <TD>{it.position}</TD>
                    <TD className="text-right font-mono">{it.qty}</TD>
                    <TD className="text-right font-mono">{Number(it.unit_price).toFixed(2)}</TD>
                    <TD className="text-right font-mono">{Number(it.total_price).toFixed(2)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>

          <div>
            <p className="mb-1 text-xs font-medium text-foreground-muted">Оплаты</p>
            {data.payments.length === 0 ? (
              <p className="text-sm italic text-foreground-muted">Нет</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {data.payments.map((p) => (
                  <li key={p.id} className="flex flex-wrap justify-between gap-2">
                    <span>
                      {paymentMethodLabel[p.payment_method as PaymentMethodRead] ??
                        p.payment_method}
                    </span>
                    <span className="font-mono">
                      {Number(p.amount).toFixed(2)} {p.currency}
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
  return v ? new Date(v).toLocaleString("ru-RU") : "—";
}
