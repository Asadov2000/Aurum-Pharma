import { isAxiosError } from "axios";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Button,
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
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { useTenantOperationalSettingsQuery } from "@/features/foundation/queries";
import { type Sale, type SaleDetails } from "@/features/pos/types";
import { describeApiError } from "@/lib/errorMessages";

import {
  beginRefundAttemptReconciliation,
  confirmRefundAttempt,
  createRefundAttempt,
  getRefundAttempt,
  getRefundResult,
  voidRefundAttempt,
} from "./api";
import { useRefundSale } from "./queries";
import {
  clearPendingRefundOperation,
  createPendingRefundOperation,
  loadPendingRefundOperation,
  savePendingRefundAttemptId,
  type PendingRefundOperation,
} from "./refundOperation";
import {
  type ElectronicRefundMethod,
  type RefundAttempt,
  type RefundAttemptConfirmation,
} from "./types";

interface LineState {
  selected: boolean;
  qty: string;
}

interface TerminalReferenceState {
  terminalId: string;
  documentNumber: string;
}

type TerminalReferences = Partial<Record<ElectronicRefundMethod, TerminalReferenceState>>;
type RefundLookupResult =
  | { status: "found"; sale: Sale }
  | { status: "missing" }
  | { status: "unknown" };

const METHOD_LABELS: Record<ElectronicRefundMethod, string> = {
  card: "Карта",
  qr: "QR-код",
  bank_transfer: "Банковский перевод",
};
const refundDateFormatter = new Intl.DateTimeFormat("ru-RU");

function sameRefundItems(
  left: { sale_item_id: string; qty: string }[],
  right: { sale_item_id: string; qty: string }[],
): boolean {
  if (left.length !== right.length) return false;
  const rightById = new Map(right.map((item) => [item.sale_item_id, Number(item.qty)]));
  return left.every(
    (item) => Math.abs((rightById.get(item.sale_item_id) ?? -1) - Number(item.qty)) < 0.0005,
  );
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

function referencesFromAttempt(attempt: RefundAttempt): TerminalReferences {
  return Object.fromEntries(
    attempt.payments.map((payment) => [
      payment.payment_method,
      {
        terminalId: payment.terminal_id ?? "",
        documentNumber: payment.document_number ?? "",
      },
    ]),
  ) as TerminalReferences;
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
  const { user } = useAuth();
  const canConfirmExternal = hasPermission(user, "pos.refund_external_confirm");
  const refund = useRefundSale();
  const settings = useTenantOperationalSettingsQuery();
  const reasonMode = settings.data?.refund_reason_mode;
  const [topError, setTopError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [initialPendingOperation] = useState(() => loadPendingRefundOperation(sale.id));
  const [refundAttempt, setRefundAttempt] = useState<RefundAttempt | null>(null);
  const [terminalReferences, setTerminalReferences] = useState<TerminalReferences>({});
  const [reconciling, setReconciling] = useState(false);
  const [attemptBusy, setAttemptBusy] = useState(false);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const [financialOperationPending, setFinancialOperationPending] = useState(
    initialPendingOperation !== null,
  );
  const pendingOperationRef = useRef<PendingRefundOperation | null>(initialPendingOperation);
  const recoveryStartedRef = useRef(false);
  const submittingRef = useRef(false);
  const requiresExternalRefund = useMemo(
    () => sale.payments.some((payment) => payment.payment_method !== "cash"),
    [sale.payments],
  );
  const [lines, setLines] = useState<Record<string, LineState>>(() =>
    Object.fromEntries(
      sale.items.map((item) => {
        const available = Math.max(0, Number(item.qty) - Number(item.refunded_qty ?? "0"));
        const pendingLine = initialPendingOperation?.items.find(
          (candidate) => candidate.sale_item_id === item.id,
        );
        return [
          item.id,
          {
            selected:
              initialPendingOperation !== null ? pendingLine !== undefined : available > 0.0005,
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

  const applyRefundAttempt = useCallback((attempt: RefundAttempt) => {
    setRefundAttempt(attempt);
    setTerminalReferences(referencesFromAttempt(attempt));
  }, []);

  const restoreRefundAttempt = useCallback(
    async (operation: PendingRefundOperation): Promise<boolean> => {
      if (!operation.refundAttemptOperationId) return true;
      setAttemptBusy(true);
      try {
        let attempt = operation.refundAttemptId
          ? await getRefundAttempt(operation.refundAttemptId)
          : await createRefundAttempt(
              operation.parentSaleId,
              operation.refundAttemptOperationId,
              operation.items,
            );
        if (!sameRefundItems(attempt.items, operation.items)) {
          setRecoveryBlocked(true);
          setTopError(
            "Сохранённая заявка не совпадает с выбранными позициями. Обратитесь к администратору.",
          );
          return false;
        }
        if (!operation.refundAttemptId) {
          const updated = savePendingRefundAttemptId(operation, attempt.id);
          if (!updated) {
            setRecoveryBlocked(true);
            setTopError(
              "Не удалось безопасно сохранить номер заявки. Не повторяйте возврат во внешнем терминале.",
            );
            return false;
          }
          pendingOperationRef.current = updated;
        }
        if (attempt.status === "voided") {
          clearPendingRefundOperation(operation.parentSaleId, operation.operationId);
          pendingOperationRef.current = null;
          setFinancialOperationPending(false);
          setRefundAttempt(null);
          setTopError("Предыдущая заявка отменена. Можно создать новый возврат.");
          return true;
        }
        if (attempt.status === "consumed") {
          setRecoveryBlocked(true);
          setTopError(
            "Возврат уже проведён, но чек пока не найден. Не повторяйте операцию и обратитесь к администратору.",
          );
          return false;
        }
        if (attempt.status === "pending") {
          attempt = await beginRefundAttemptReconciliation(attempt.id);
        }
        applyRefundAttempt(attempt);
        setRecoveryBlocked(false);
        setTopError(
          attempt.status === "requires_reconciliation"
            ? "Заявка восстановлена и требует сверки. Не повторяйте возврат в терминале; проверьте его документ."
            : "Подтверждение восстановлено. Можно повторить оформление чека возврата.",
        );
        return true;
      } catch (error) {
        setRecoveryBlocked(true);
        setTopError(
          describeApiError(
            error,
            "Не удалось восстановить заявку возврата. Не повторяйте возврат денег во внешнем терминале.",
          ),
        );
        return false;
      } finally {
        setAttemptBusy(false);
      }
    },
    [applyRefundAttempt],
  );

  const reconcileRefund = useCallback(
    async (operation: PendingRefundOperation): Promise<RefundLookupResult["status"]> => {
      setReconciling(true);
      const result = await lookupRefundResult(operation.operationId);
      setReconciling(false);
      if (result.status === "found") {
        finishRefund(operation, result.sale.id);
        return "found";
      }
      if (result.status === "missing") {
        setRecoveryBlocked(false);
        if (operation.refundAttemptOperationId) {
          await restoreRefundAttempt(operation);
        } else {
          setTopError(
            "Предыдущий возврат не найден на сервере. Можно безопасно повторить отправку.",
          );
        }
        return "missing";
      }
      setRecoveryBlocked(true);
      setTopError(
        "Не удалось проверить результат возврата. Не повторяйте возврат денег, пока связь не восстановится.",
      );
      return "unknown";
    },
    [finishRefund, restoreRefundAttempt],
  );

  useEffect(() => {
    const operation = pendingOperationRef.current;
    if (!operation || recoveryStartedRef.current) return;
    recoveryStartedRef.current = true;
    setRecoveryBlocked(true);
    void reconcileRefund(operation);
  }, [reconcileRefund]);

  const setLine = (id: string, patch: Partial<LineState>) =>
    setLines((previous) => ({ ...previous, [id]: { ...previous[id]!, ...patch } }));

  const selectedItems = () =>
    sale.items
      .filter((item) => lines[item.id]?.selected)
      .map((item) => ({ sale_item_id: item.id, qty: lines[item.id]!.qty }));

  const validateForm = (): { sale_item_id: string; qty: string }[] | null => {
    if (!reasonMode) {
      setTopError("Не удалось подтвердить настройки возврата. Обновите страницу.");
      return null;
    }
    if (
      (reasonMode === "required" || reasonMode === "required_with_text") &&
      reason.trim().length === 0
    ) {
      setTopError("Укажите причину возврата.");
      return null;
    }
    if (reasonMode === "required_with_text" && comment.trim().length === 0) {
      setTopError("Добавьте комментарий к возврату.");
      return null;
    }
    const chosen = selectedItems();
    if (chosen.length === 0) {
      setTopError("Выберите хотя бы одну позицию для возврата.");
      return null;
    }
    for (const item of sale.items) {
      const line = lines[item.id];
      if (!line?.selected) continue;
      const qty = Number(line.qty);
      const available = Math.max(0, Number(item.qty) - Number(item.refunded_qty ?? "0"));
      if (!(qty > 0)) {
        setTopError("Количество возврата должно быть больше 0.");
        return null;
      }
      if (qty - available > 0.0005) {
        setTopError("Количество возврата больше доступного остатка.");
        return null;
      }
    }
    return chosen;
  };

  const persistAttempt = (
    operation: PendingRefundOperation,
    attempt: RefundAttempt,
  ): PendingRefundOperation | null => {
    if (operation.refundAttemptId === attempt.id) return operation;
    const updated = savePendingRefundAttemptId(operation, attempt.id);
    if (!updated) {
      setRecoveryBlocked(true);
      setTopError("Не удалось безопасно сохранить заявку. Денежная операция остановлена.");
      return null;
    }
    pendingOperationRef.current = updated;
    return updated;
  };

  const terminalConfirmations = (): RefundAttemptConfirmation[] | null => {
    if (!refundAttempt) return null;
    const confirmations: RefundAttemptConfirmation[] = [];
    for (const payment of refundAttempt.payments) {
      const reference = terminalReferences[payment.payment_method];
      const terminalId = reference?.terminalId.trim() ?? "";
      const documentNumber = reference?.documentNumber.trim() ?? "";
      if (!terminalId || !documentNumber) {
        setTopError("Для каждого электронного способа укажите терминал и номер документа.");
        return null;
      }
      confirmations.push({
        payment_method: payment.payment_method,
        terminal_id: terminalId,
        document_number: documentNumber,
      });
    }
    return confirmations;
  };

  const onSubmit = async () => {
    if (submittingRef.current || reconciling || recoveryBlocked || attemptBusy) return;
    const chosen = validateForm();
    if (!chosen) return;
    const currentOperation = pendingOperationRef.current;
    if (currentOperation && !sameRefundItems(currentOperation.items, chosen)) {
      setRecoveryBlocked(true);
      setTopError("Сохранённый состав возврата не совпадает с чеком. Обратитесь к администратору.");
      return;
    }
    let operation =
      currentOperation ?? createPendingRefundOperation(sale.id, chosen, requiresExternalRefund);
    if (!operation) {
      setTopError("Локальное хранилище недоступно. Возврат не отправлен.");
      return;
    }
    pendingOperationRef.current = operation;
    setFinancialOperationPending(true);
    setTopError(null);
    submittingRef.current = true;
    try {
      let activeAttempt = refundAttempt;
      if (requiresExternalRefund && !activeAttempt) {
        if (!operation.refundAttemptOperationId) {
          setRecoveryBlocked(true);
          setTopError("Маркер заявки повреждён. Денежная операция остановлена.");
          return;
        }
        const created = await createRefundAttempt(
          sale.id,
          operation.refundAttemptOperationId,
          operation.items,
        );
        const updated = persistAttempt(operation, created);
        if (!updated) return;
        operation = updated;
        activeAttempt =
          created.status === "pending"
            ? await beginRefundAttemptReconciliation(created.id)
            : created;
        applyRefundAttempt(activeAttempt);
        setTopError(
          canConfirmExternal
            ? "Сумма зафиксирована для сверки. Выполните возврат в терминале один раз и внесите реквизиты документа."
            : "Заявка создана. Для подтверждения пригласите сотрудника с соответствующим правом.",
        );
        return;
      }
      if (activeAttempt?.status === "pending") {
        activeAttempt = await beginRefundAttemptReconciliation(activeAttempt.id);
        applyRefundAttempt(activeAttempt);
        setTopError(
          "Сумма зафиксирована для сверки. Выполните возврат в терминале один раз и внесите реквизиты документа.",
        );
        return;
      }
      if (activeAttempt?.status === "requires_reconciliation") {
        if (!canConfirmExternal) {
          setTopError(
            "У вас нет права подтверждать электронные возвраты. Пригласите управляющего.",
          );
          return;
        }
        const confirmations = terminalConfirmations();
        if (!confirmations) return;
        activeAttempt = await confirmRefundAttempt(activeAttempt.id, confirmations);
        applyRefundAttempt(activeAttempt);
      }
      if (requiresExternalRefund && activeAttempt?.status !== "confirmed") {
        setTopError("Электронный возврат ещё не подтверждён.");
        return;
      }
      const returnSale = await refund.mutateAsync({
        parentSaleId: sale.id,
        payload: {
          operation_id: operation.operationId,
          items: operation.items,
          reason: reason.trim() || null,
          comment: comment.trim() || null,
          refund_attempt_id: activeAttempt?.id ?? null,
        },
      });
      finishRefund(operation, returnSale.id);
    } catch (error) {
      const status = await reconcileRefund(operation);
      if (status === "missing" && isAxiosError(error) && error.response !== undefined) {
        setRecoveryBlocked(false);
        setTopError(describeApiError(error, "Не удалось оформить возврат."));
        if (!requiresExternalRefund) {
          clearPendingRefundOperation(sale.id, operation.operationId);
          pendingOperationRef.current = null;
          setFinancialOperationPending(false);
        }
      }
    } finally {
      submittingRef.current = false;
    }
  };

  const cancelPendingAttempt = async () => {
    const operation = pendingOperationRef.current;
    if (
      !operation ||
      !refundAttempt ||
      !["pending", "requires_reconciliation"].includes(refundAttempt.status) ||
      (refundAttempt.status === "requires_reconciliation" && !canConfirmExternal) ||
      attemptBusy
    )
      return;
    setAttemptBusy(true);
    setTopError(null);
    try {
      await voidRefundAttempt(refundAttempt.id);
      clearPendingRefundOperation(sale.id, operation.operationId);
      pendingOperationRef.current = null;
      setRefundAttempt(null);
      setTerminalReferences({});
      setFinancialOperationPending(false);
      setRecoveryBlocked(false);
      setTopError("Заявка отменена. Деньги во внешнем терминале возвращать не нужно.");
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось отменить заявку возврата."));
    } finally {
      setAttemptBusy(false);
    }
  };

  const buttonLabel =
    requiresExternalRefund && !refundAttempt
      ? "Рассчитать возврат"
      : refundAttempt?.status === "pending" || refundAttempt?.status === "requires_reconciliation"
        ? canConfirmExternal
          ? "Подтвердить и оформить"
          : "Ожидает подтверждения"
        : "Оформить возврат";

  return (
    <Modal open onClose={onClose} title={`Возврат по чеку № ${sale.receipt_number ?? "—"}`}>
      <div className="space-y-4">
        <Table>
          <THead>
            <TR>
              <TH className="w-10" />
              <TH>Позиция</TH>
              <TH className="text-right">Доступно</TH>
              <TH className="text-right">Вернуть</TH>
            </TR>
          </THead>
          <TBody>
            {sale.items.map((item) => {
              const line = lines[item.id]!;
              const available = Math.max(0, Number(item.qty) - Number(item.refunded_qty ?? "0"));
              return (
                <TR key={item.id}>
                  <TD>
                    <input
                      type="checkbox"
                      aria-label={`Вернуть позицию ${item.position}`}
                      checked={line.selected}
                      disabled={available <= 0.0005 || financialOperationPending}
                      onChange={(event) => setLine(item.id, { selected: event.target.checked })}
                    />
                  </TD>
                  <TD>
                    <p className="font-medium">Позиция {item.position}</p>
                    <p className="text-xs text-foreground-muted">
                      Партия {item.batch_number ?? "без номера"}
                      {item.expires_at ? ` · до ${formatRefundDate(item.expires_at)}` : ""}
                    </p>
                  </TD>
                  <TD className="text-right font-mono">{available.toFixed(3)}</TD>
                  <TD className="text-right">
                    <Input
                      type="text"
                      inputMode="decimal"
                      value={line.qty}
                      disabled={!line.selected || financialOperationPending}
                      onChange={(event) => {
                        const value = event.target.value.replace(",", ".");
                        if (/^\d{0,11}(?:\.\d{0,3})?$/.test(value)) {
                          setLine(item.id, { qty: value });
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
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="refund_reason">
                Причина
                {reasonMode === "required" || reasonMode === "required_with_text" ? " *" : ""}
              </Label>
              <Input
                id="refund_reason"
                value={reason}
                maxLength={500}
                onChange={(event) => setReason(event.target.value)}
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
                onChange={(event) => setComment(event.target.value)}
              />
            </div>
          </div>
        ) : null}

        {requiresExternalRefund ? (
          <section className="rounded-lg border border-warning/40 bg-warning/5 p-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold">Контроль электронного возврата</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  Сначала система фиксирует сумму. Затем возврат выполняется в терминале и
                  подтверждается его документом.
                </p>
              </div>
              {refundAttempt ? (
                <span className="whitespace-nowrap text-sm font-medium">
                  {refundAttempt.external_amount} TJS
                </span>
              ) : null}
            </div>
            {refundAttempt ? (
              <div className="mt-3 space-y-3">
                {refundAttempt.payments.map((payment) => {
                  const reference = terminalReferences[payment.payment_method] ?? {
                    terminalId: "",
                    documentNumber: "",
                  };
                  const disabled =
                    refundAttempt.status !== "requires_reconciliation" || !canConfirmExternal;
                  return (
                    <div
                      key={payment.payment_method}
                      className="grid grid-cols-1 gap-2 border-t border-border/70 pt-3 sm:grid-cols-[minmax(8rem,0.8fr)_1fr_1.4fr]"
                    >
                      <div className="text-sm font-medium">
                        {METHOD_LABELS[payment.payment_method]}
                        <span className="block text-xs font-normal text-muted-foreground">
                          {payment.amount} TJS
                        </span>
                      </div>
                      <div>
                        <Label htmlFor={`terminal-${payment.payment_method}`}>Терминал</Label>
                        <Input
                          id={`terminal-${payment.payment_method}`}
                          value={reference.terminalId}
                          disabled={disabled}
                          maxLength={64}
                          autoComplete="off"
                          onChange={(event) =>
                            setTerminalReferences((previous) => ({
                              ...previous,
                              [payment.payment_method]: {
                                ...reference,
                                terminalId: event.target.value,
                              },
                            }))
                          }
                        />
                      </div>
                      <div>
                        <Label htmlFor={`document-${payment.payment_method}`}>
                          Номер документа
                        </Label>
                        <Input
                          id={`document-${payment.payment_method}`}
                          value={reference.documentNumber}
                          disabled={disabled}
                          maxLength={128}
                          autoComplete="off"
                          onChange={(event) =>
                            setTerminalReferences((previous) => ({
                              ...previous,
                              [payment.payment_method]: {
                                ...reference,
                                documentNumber: event.target.value,
                              },
                            }))
                          }
                        />
                      </div>
                    </div>
                  );
                })}
                {!canConfirmExternal &&
                (refundAttempt.status === "pending" ||
                  refundAttempt.status === "requires_reconciliation") ? (
                  <p className="text-sm text-warning-foreground">
                    Подтвердить заявку может только сотрудник с правом электронного возврата.
                  </p>
                ) : null}
              </div>
            ) : null}
          </section>
        ) : null}

        {topError ? <p className="text-sm text-danger">{topError}</p> : null}

        <div
          role="note"
          className="rounded-lg border border-warning/40 bg-warning-subtle px-4 py-3 text-sm text-warning-foreground"
        >
          <p className="font-medium">Возвращённый товар не поступит в продажу.</p>
          <p className="mt-1">Поместите упаковку отдельно и передайте ответственному сотруднику.</p>
        </div>

        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={reconciling || attemptBusy}>
            Закрыть
          </Button>
          {refundAttempt?.status === "pending" ||
          (refundAttempt?.status === "requires_reconciliation" && canConfirmExternal) ? (
            <Button
              variant="secondary"
              onClick={() => void cancelPendingAttempt()}
              isLoading={attemptBusy}
            >
              Терминал проверен, возврата нет
            </Button>
          ) : null}
          {recoveryBlocked ? (
            <Button
              variant="secondary"
              onClick={() => {
                const operation = pendingOperationRef.current;
                if (operation) void reconcileRefund(operation);
              }}
              isLoading={reconciling}
            >
              Проверить результат
            </Button>
          ) : null}
          <Button
            onClick={() => void onSubmit()}
            isLoading={refund.isPending || reconciling || attemptBusy}
            disabled={
              !reasonMode ||
              settings.isLoading ||
              recoveryBlocked ||
              reconciling ||
              attemptBusy ||
              refund.isPending ||
              ((refundAttempt?.status === "pending" ||
                refundAttempt?.status === "requires_reconciliation") &&
                !canConfirmExternal)
            }
          >
            {buttonLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function formatRefundDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return refundDateFormatter.format(new Date(year, month - 1, day, 12));
}
