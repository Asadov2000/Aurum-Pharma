import { isAxiosError } from "axios";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  Button,
  Checkbox,
  Input,
  Label,
  Modal,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Textarea,
} from "@/components/ui";
import { type Sale, type SaleDetails } from "@/features/pos/types";
import { useTenantSettingsQuery } from "@/features/foundation/queries";
import { describeApiError } from "@/lib/errorMessages";

import { getRefundResult } from "./api";
import { useRefundSale } from "./queries";
import {
  clearPendingRefundOperation,
  createPendingRefundOperation,
  loadPendingRefundOperation,
  type PendingRefundOperation,
} from "./refundOperation";

interface LineState {
  selected: boolean;
  qty: string;
}

type RefundLookupResult =
  | { status: "found"; sale: Sale }
  | { status: "missing" }
  | { status: "unknown" };

function sameRefundItems(
  left: { sale_item_id: string; qty: string }[],
  right: { sale_item_id: string; qty: string }[],
): boolean {
  if (left.length !== right.length) return false;
  const rightById = new Map(right.map((item) => [item.sale_item_id, item.qty]));
  return left.every((item) => rightById.get(item.sale_item_id) === item.qty);
}

async function lookupRefundResult(operationId: string): Promise<RefundLookupResult> {
  try {
    return { status: "found", sale: await getRefundResult(operationId) };
  } catch (error) {
    if (isAxiosError(error) && error.response?.status === 404) {
      return { status: "missing" };
    }
    return { status: "unknown" };
  }
}

export function RefundModal({
  sale,
  onClose,
  onRefunded,
}: {
  sale: SaleDetails;
  onClose: () => void;
  onRefunded: (returnSaleId: string) => void;
}): JSX.Element {
  const refund = useRefundSale();
  const settings = useTenantSettingsQuery();
  const reasonMode = settings.data?.refund_reason_mode;
  const [topError, setTopError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [initialPendingOperation] = useState(() => loadPendingRefundOperation(sale.id));
  const [externalRefundConfirmed, setExternalRefundConfirmed] = useState(
    initialPendingOperation?.externalRefundConfirmed ?? false,
  );
  const [reconciling, setReconciling] = useState(false);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const [financialOperationPending, setFinancialOperationPending] = useState(
    initialPendingOperation !== null,
  );
  const pendingOperationRef = useRef<PendingRefundOperation | null>(initialPendingOperation);
  const recoveryStartedRef = useRef(false);
  const submittingRef = useRef(false);
  const externalPaymentMethods = Array.from(
    new Set(
      sale.payments
        .map((payment) => payment.payment_method)
        .filter((method) => method !== "cash"),
    ),
  );
  const requiresExternalRefund = externalPaymentMethods.length > 0;
  // Default: every line selected at full quantity (the common "вернуть всё").
  const [lines, setLines] = useState<Record<string, LineState>>(() =>
    Object.fromEntries(
      sale.items.map((it) => {
        const available = Math.max(0, Number(it.qty) - Number(it.refunded_qty ?? "0"));
        const pendingLine = initialPendingOperation?.items.find(
          (item) => item.sale_item_id === it.id,
        );
        return [
          it.id,
          {
            selected:
              initialPendingOperation !== null
                ? pendingLine !== undefined
                : available > 0.0005,
            qty:
              pendingLine?.qty ??
              (available > 0.0005 ? available.toFixed(3).replace(/\.?0+$/, "") : "0"),
          },
        ];
      }),
    ),
  );

  const finishRefund = useCallback(
    (operation: PendingRefundOperation, returnSaleId: string) => {
      clearPendingRefundOperation(sale.id, operation.operationId);
      pendingOperationRef.current = null;
      setFinancialOperationPending(false);
      setRecoveryBlocked(false);
      onRefunded(returnSaleId);
    },
    [onRefunded, sale.id],
  );

  const verifyPendingRefund = useCallback(
    async (
      operation: PendingRefundOperation,
      missingMessage: string,
    ): Promise<RefundLookupResult["status"]> => {
      setReconciling(true);
      const result = await lookupRefundResult(operation.operationId);
      setReconciling(false);
      if (result.status === "found") {
        finishRefund(operation, result.sale.id);
        return "found";
      }
      if (result.status === "missing") {
        setRecoveryBlocked(false);
        setTopError(missingMessage);
        return "missing";
      }
      setRecoveryBlocked(true);
      setTopError(
        "Не удалось проверить результат возврата. Не повторяйте возврат денег во внешнем терминале, пока связь не восстановится.",
      );
      return "unknown";
    },
    [finishRefund],
  );

  useEffect(() => {
    const operation = pendingOperationRef.current;
    if (!operation || recoveryStartedRef.current) return;
    recoveryStartedRef.current = true;
    setRecoveryBlocked(true);
    void verifyPendingRefund(
      operation,
      "Предыдущий возврат не найден на сервере. Проверьте внешний терминал и затем повторите оформление с тем же расчётом.",
    );
  }, [verifyPendingRefund]);

  const setLine = (id: string, patch: Partial<LineState>) =>
    setLines((prev) => ({ ...prev, [id]: { ...prev[id]!, ...patch } }));

  const onSubmit = async () => {
    if (submittingRef.current || reconciling || recoveryBlocked) return;
    if (!reasonMode) {
      setTopError("Не удалось подтвердить настройки возврата. Обновите страницу.");
      return;
    }
    if (
      (reasonMode === "required" || reasonMode === "required_with_text") &&
      reason.trim().length === 0
    ) {
      setTopError("Укажите причину возврата.");
      return;
    }
    if (reasonMode === "required_with_text" && comment.trim().length === 0) {
      setTopError("Добавьте комментарий к возврату.");
      return;
    }
    if (requiresExternalRefund && !externalRefundConfirmed) {
      setTopError("Подтвердите возврат денег во внешнем банковском или QR-терминале.");
      return;
    }

    const chosen = sale.items
      .filter((it) => lines[it.id]?.selected)
      .map((it) => ({ sale_item_id: it.id, qty: lines[it.id]!.qty }));

    if (chosen.length === 0) {
      setTopError("Выберите хотя бы одну позицию для возврата.");
      return;
    }
    for (const it of sale.items) {
      const l = lines[it.id];
      if (!l?.selected) continue;
      const q = Number(l.qty);
      const available = Math.max(0, Number(it.qty) - Number(it.refunded_qty ?? "0"));
      if (!(q > 0)) {
        setTopError("Количество возврата должно быть больше 0.");
        return;
      }
      if (q - available > 0.0005) {
        setTopError("Количество возврата больше доступного остатка.");
        return;
      }
    }

    const currentOperation = pendingOperationRef.current;
    if (
      currentOperation !== null &&
      !sameRefundItems(currentOperation.items, chosen)
    ) {
      setRecoveryBlocked(true);
      setTopError(
        "Сохранённый состав возврата не совпадает с чеком. Не повторяйте возврат денег; выполните сверку с администратором.",
      );
      return;
    }
    const operation =
      currentOperation ??
      createPendingRefundOperation(sale.id, chosen, externalRefundConfirmed);
    if (!operation) {
      setTopError(
        "Локальное хранилище недоступно. Возврат не отправлен: перезапустите приложение или освободите место.",
      );
      return;
    }
    pendingOperationRef.current = operation;
    setFinancialOperationPending(true);
    setTopError(null);
    submittingRef.current = true;
    try {
      const returnSale = await refund.mutateAsync({
        parentSaleId: sale.id,
        payload: {
          operation_id: operation.operationId,
          items: operation.items,
          reason: reason.trim() || null,
          comment: comment.trim() || null,
          external_refund_confirmed: operation.externalRefundConfirmed,
        },
      });
      finishRefund(operation, returnSale.id);
    } catch (err) {
      const lookupStatus = await verifyPendingRefund(
        operation,
        "Возврат не найден на сервере. Проверьте внешний терминал и повторите оформление.",
      );
      if (lookupStatus === "missing" && isAxiosError(err) && err.response !== undefined) {
        clearPendingRefundOperation(sale.id, operation.operationId);
        pendingOperationRef.current = null;
        setTopError(describeApiError(err, "Не удалось оформить возврат."));
      }
    } finally {
      submittingRef.current = false;
    }
  };

  return (
    <Modal open onClose={onClose} title={`Возврат по чеку № ${sale.receipt_number ?? "—"}`}>
      <div className="space-y-4">
        <Table>
          <THead>
            <TR>
              <TH className="w-10"></TH>
              <TH>Позиция</TH>
              <TH className="text-right">Доступно</TH>
              <TH className="text-right">Вернуть</TH>
            </TR>
          </THead>
          <TBody>
            {sale.items.map((it) => {
              const l = lines[it.id]!;
              const available = Math.max(
                0,
                Number(it.qty) - Number(it.refunded_qty ?? "0"),
              );
              return (
                <TR key={it.id}>
                  <TD>
                    <input
                      type="checkbox"
                      aria-label={`Вернуть позицию ${it.position}`}
                      checked={l.selected}
                      disabled={available <= 0.0005 || financialOperationPending}
                      onChange={(e) => setLine(it.id, { selected: e.target.checked })}
                    />
                  </TD>
                  <TD className="font-mono text-xs">{it.catalog_id.slice(0, 8)}</TD>
                  <TD className="text-right font-mono">{available.toFixed(3)}</TD>
                  <TD className="text-right">
                    <Input
                      type="text"
                      inputMode="decimal"
                      value={l.qty}
                      disabled={!l.selected || financialOperationPending}
                      onChange={(e) => {
                        const value = e.target.value.replace(",", ".");
                        if (/^\d{0,11}(?:\.\d{0,3})?$/.test(value)) {
                          setLine(it.id, { qty: value });
                        }
                      }}
                      className="w-20 text-right"
                    />
                  </TD>
                </TR>
              );
            })}
          </TBody>
        </Table>

        {reasonMode !== "off" ? (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="refund_reason">
                Причина
                {reasonMode === "required" || reasonMode === "required_with_text" ? " *" : ""}
              </Label>
              <Input
                id="refund_reason"
                value={reason}
                maxLength={500}
                onChange={(e) => setReason(e.target.value)}
                placeholder="брак, ошибка кассира…"
              />
            </div>
            <div>
              <Label htmlFor="refund_comment">
                Комментарий{reasonMode === "required_with_text" ? " *" : ""}
              </Label>
              <Textarea
                id="refund_comment"
                rows={1}
                value={comment}
                maxLength={2000}
                onChange={(e) => setComment(e.target.value)}
              />
            </div>
          </div>
        ) : null}

        {requiresExternalRefund ? (
          <label className="flex items-start gap-3 rounded-lg border border-warning/40 bg-warning/5 p-3">
            <Checkbox
              checked={externalRefundConfirmed}
              disabled={financialOperationPending}
              onChange={(event) => setExternalRefundConfirmed(event.target.checked)}
              className="mt-0.5"
            />
            <span className="text-sm">
              Подтверждаю, что деньги по{" "}
              {externalPaymentMethods
                .map((method) =>
                  method === "card"
                    ? "карте"
                    : method === "qr"
                      ? "QR"
                      : "банковскому переводу",
                )
                .join(" и ")}{" "}
              возвращены покупателю во внешнем терминале
            </span>
          </label>
        ) : null}

        {topError && <p className="text-sm text-danger">{topError}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={reconciling}>
            Отмена
          </Button>
          {recoveryBlocked ? (
            <Button
              variant="secondary"
              onClick={() => {
                const operation = pendingOperationRef.current;
                if (!operation) return;
                void verifyPendingRefund(
                  operation,
                  "Возврат не найден на сервере. Проверьте внешний терминал и повторите оформление.",
                );
              }}
              isLoading={reconciling}
            >
              Проверить результат
            </Button>
          ) : null}
          <Button
            onClick={() => void onSubmit()}
            isLoading={refund.isPending || reconciling}
            disabled={
              !reasonMode ||
              settings.isLoading ||
              recoveryBlocked ||
              reconciling ||
              refund.isPending
            }
          >
            Оформить возврат
          </Button>
        </div>
      </div>
    </Modal>
  );
}
