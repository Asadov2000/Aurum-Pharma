import { isAxiosError } from "axios";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Button,
  ConfirmDialog,
  Input,
  Label,
  Modal,
  Select,
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
import { REFUND_REASON_OPTIONS, type RefundReasonCode } from "@/lib/refundReasons";

import {
  beginRefundAttemptReconciliation,
  confirmRefundAttempt,
  createRefundAttempt,
  getRefundAttempt,
  getRefundResult,
  voidRefundAttempt,
} from "./api";
import { useActiveRefundAttemptQuery, useRefundSale } from "./queries";
import {
  clearPendingRefundOperation,
  createPendingRefundOperation,
  loadPendingRefundOperation,
  saveRecoveredPendingRefundOperation,
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

function refundAttemptMatchesSale(attempt: RefundAttempt, sale: SaleDetails): boolean {
  if (
    attempt.parent_sale_id !== sale.id ||
    attempt.tenant_id !== sale.tenant_id ||
    attempt.register_id !== sale.register_id ||
    attempt.items.length === 0 ||
    new Set(attempt.items.map((item) => item.sale_item_id)).size !== attempt.items.length
  ) {
    return false;
  }
  const availableById = new Map(
    sale.items.map((item) => [
      item.id,
      Math.max(0, Number(item.qty) - Number(item.refunded_qty ?? "0")),
    ]),
  );
  return attempt.items.every((item) => {
    const qty = Number(item.qty);
    const available = availableById.get(item.sale_item_id);
    return available !== undefined && Number.isFinite(qty) && qty > 0 && qty - available <= 0.0005;
  });
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
  const [topMessageTone, setTopMessageTone] = useState<"danger" | "warning" | "info">("danger");
  const [reason, setReason] = useState<RefundReasonCode | "">("");
  const [comment, setComment] = useState("");
  const [initialPendingOperation] = useState(() => loadPendingRefundOperation(sale.id));
  const [refundAttempt, setRefundAttempt] = useState<RefundAttempt | null>(null);
  const [terminalReferences, setTerminalReferences] = useState<TerminalReferences>({});
  const [reconciling, setReconciling] = useState(false);
  const [attemptBusy, setAttemptBusy] = useState(false);
  const [cancelAttemptOpen, setCancelAttemptOpen] = useState(false);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const [financialOperationPending, setFinancialOperationPending] = useState(
    initialPendingOperation !== null,
  );
  const pendingOperationRef = useRef<PendingRefundOperation | null>(initialPendingOperation);
  const recoveryStartedRef = useRef(false);
  const serverRecoveryHandledRef = useRef(false);
  const submittingRef = useRef(false);
  const requiresExternalRefund = useMemo(
    () => sale.payments.some((payment) => payment.payment_method !== "cash"),
    [sale.payments],
  );
  const shouldDiscoverServerAttempt = initialPendingOperation === null && requiresExternalRefund;
  const activeRefundAttempt = useActiveRefundAttemptQuery(sale.id, shouldDiscoverServerAttempt);
  const activeAttemptLookupInProgress =
    shouldDiscoverServerAttempt && activeRefundAttempt.isFetching;
  const financialActionsBlocked = financialOperationPending || activeAttemptLookupInProgress;
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
            selected: initialPendingOperation !== null ? pendingLine !== undefined : false,
            qty:
              pendingLine?.qty ??
              (available > 0.0005 ? available.toFixed(3).replace(/\.?0+$/, "") : "0"),
          },
        ];
      }),
    ),
  );
  const selectedSummary = useMemo(() => {
    let count = 0;
    let amount = 0;
    for (const item of sale.items) {
      const line = lines[item.id];
      if (!line?.selected) continue;
      const qty = Number(line.qty);
      if (!(qty > 0) || !(Number(item.qty) > 0)) continue;
      count += 1;
      amount += (Number(item.total_price) * qty) / Number(item.qty);
    }
    return { count, amount };
  }, [lines, sale.items]);
  const displayedRefundAmount = refundAttempt
    ? Number(refundAttempt.total_amount)
    : selectedSummary.amount;

  const finishRefund = useCallback(
    (operation: PendingRefundOperation, returnSaleId: string) => {
      clearPendingRefundOperation(sale.id, operation.operationId);
      pendingOperationRef.current = null;
      setFinancialOperationPending(false);
      setRecoveryBlocked(false);
      setCancelAttemptOpen(false);
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
          setTopMessageTone("info");
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
        setTopMessageTone(attempt.status === "requires_reconciliation" ? "warning" : "info");
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
          setTopMessageTone("info");
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

  useEffect(() => {
    if (
      !shouldDiscoverServerAttempt ||
      activeAttemptLookupInProgress ||
      serverRecoveryHandledRef.current
    ) {
      return;
    }
    serverRecoveryHandledRef.current = true;
    if (activeRefundAttempt.isError) {
      setFinancialOperationPending(true);
      setRecoveryBlocked(true);
      setTopMessageTone("danger");
      setTopError(
        "Не удалось проверить незавершённый электронный возврат. Не повторяйте возврат денег; повторите поиск после восстановления связи.",
      );
      return;
    }

    const attempt = activeRefundAttempt.data;
    if (!attempt) {
      setFinancialOperationPending(false);
      setRecoveryBlocked(false);
      setTopError(null);
      return;
    }
    if (
      !refundAttemptMatchesSale(attempt, sale) ||
      !["pending", "requires_reconciliation", "confirmed"].includes(attempt.status)
    ) {
      setFinancialOperationPending(true);
      setRecoveryBlocked(true);
      setTopMessageTone("danger");
      setTopError(
        "Серверная заявка возврата не совпадает с исходным чеком. Не повторяйте денежную операцию и обратитесь к администратору.",
      );
      return;
    }

    const operation = saveRecoveredPendingRefundOperation(sale.id, attempt);
    if (
      !operation ||
      operation.refundAttemptId !== attempt.id ||
      operation.refundAttemptOperationId !== attempt.operation_id ||
      !sameRefundItems(operation.items, attempt.items)
    ) {
      setFinancialOperationPending(true);
      setRecoveryBlocked(true);
      setTopMessageTone("danger");
      setTopError(
        "Не удалось безопасно сохранить найденную заявку. Не повторяйте возврат денег; освободите место или перезапустите приложение.",
      );
      return;
    }

    pendingOperationRef.current = operation;
    setFinancialOperationPending(true);
    setLines(
      Object.fromEntries(
        sale.items.map((item) => {
          const attemptItem = attempt.items.find((candidate) => candidate.sale_item_id === item.id);
          const available = Math.max(0, Number(item.qty) - Number(item.refunded_qty ?? "0"));
          return [
            item.id,
            {
              selected: attemptItem !== undefined,
              qty:
                attemptItem?.qty ??
                (available > 0.0005 ? available.toFixed(3).replace(/\.?0+$/, "") : "0"),
            },
          ];
        }),
      ),
    );
    applyRefundAttempt(attempt);
    setRecoveryBlocked(false);
    if (attempt.status === "pending") {
      setTopMessageTone("info");
      setTopError(
        "Найдена незавершённая заявка. Деньги ещё не возвращались: можно продолжить или отменить заявку.",
      );
    } else if (attempt.status === "requires_reconciliation") {
      setTopMessageTone("warning");
      setTopError(
        "Найдена заявка, требующая сверки. Не повторяйте возврат во внешнем терминале; проверьте его документ.",
      );
    } else {
      setTopMessageTone("info");
      setTopError("Возврат денег подтверждён. Осталось создать чек возврата.");
    }
  }, [
    activeAttemptLookupInProgress,
    activeRefundAttempt.data,
    activeRefundAttempt.isError,
    applyRefundAttempt,
    sale,
    shouldDiscoverServerAttempt,
  ]);

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
    if (
      submittingRef.current ||
      reconciling ||
      recoveryBlocked ||
      attemptBusy ||
      activeAttemptLookupInProgress
    )
      return;
    setTopMessageTone("danger");
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
        setTopMessageTone(canConfirmExternal ? "warning" : "info");
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
        setTopMessageTone("warning");
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
          reason: reason || null,
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
      setCancelAttemptOpen(false);
      setTopMessageTone("info");
      setTopError("Заявка отменена. Деньги во внешнем терминале возвращать не нужно.");
    } catch (error) {
      setTopMessageTone("danger");
      setTopError(describeApiError(error, "Не удалось отменить заявку возврата."));
    } finally {
      setAttemptBusy(false);
    }
  };

  const buttonLabel =
    requiresExternalRefund && !refundAttempt
      ? "Зафиксировать сумму возврата"
      : refundAttempt?.status === "pending" || refundAttempt?.status === "requires_reconciliation"
        ? canConfirmExternal
          ? "Подтвердить возврат и создать чек"
          : "Ожидает подтверждения"
        : `Вернуть выбранное · ${formatRefundMoney(selectedSummary.amount)} TJS`;

  return (
    <Modal
      open
      onClose={onClose}
      title={`Возврат по чеку № ${sale.receipt_number ?? "—"}`}
      className="max-w-4xl"
    >
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
                    <label className="inline-flex size-11 cursor-pointer items-center justify-center">
                      <input
                        type="checkbox"
                        aria-label={`Вернуть ${item.name ?? `товар из строки ${item.position}`}`}
                        checked={line.selected}
                        disabled={available <= 0.0005 || financialActionsBlocked}
                        onChange={(event) => setLine(item.id, { selected: event.target.checked })}
                        className="size-5 accent-primary"
                      />
                    </label>
                  </TD>
                  <TD>
                    <p className="font-medium">{item.name ?? `Товар, строка ${item.position}`}</p>
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
                      aria-label={`Количество для возврата: ${item.name ?? `товар из строки ${item.position}`}`}
                      value={line.qty}
                      disabled={!line.selected || financialActionsBlocked}
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

        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-background px-4 py-3"
        >
          <div>
            <p className="text-sm font-medium">
              {selectedSummary.count > 0
                ? `Выбрано товаров: ${selectedSummary.count}`
                : "Выберите товары, которые покупатель возвращает"}
            </p>
            <p className="mt-0.5 text-xs text-foreground-muted">
              Количество и итог можно проверить до создания возвратного чека.
            </p>
          </div>
          <p className="font-mono text-lg font-semibold tabular-nums">
            {refundAttempt ? "К возврату" : "Предварительно"}:{" "}
            {formatRefundMoney(displayedRefundAmount)}
            {" TJS"}
          </p>
        </div>

        {reasonMode !== "off" ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="refund_reason">
                Причина
                {reasonMode === "required" || reasonMode === "required_with_text" ? " *" : ""}
              </Label>
              <Select
                id="refund_reason"
                value={reason}
                onChange={(event) => setReason(event.target.value as RefundReasonCode | "")}
              >
                <option value="">Не выбрана</option>
                {REFUND_REASON_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="refund_comment">
                Комментарий{reasonMode === "required_with_text" ? " *" : ""}
              </Label>
              <Textarea
                id="refund_comment"
                rows={1}
                value={comment}
                maxLength={500}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Только служебное пояснение, без ФИО, телефона и данных покупателя"
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

        {topError ? (
          <p
            role={topMessageTone === "danger" ? "alert" : "status"}
            className={
              topMessageTone === "danger"
                ? "rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm text-danger-foreground"
                : topMessageTone === "warning"
                  ? "rounded-lg border border-warning/40 bg-warning-subtle px-4 py-3 text-sm text-warning-foreground"
                  : "rounded-lg border border-info/30 bg-info-subtle px-4 py-3 text-sm text-info-foreground"
            }
          >
            {topError}
          </p>
        ) : null}

        {activeAttemptLookupInProgress ? (
          <p
            role="status"
            className="rounded-lg border border-info/30 bg-info-subtle px-4 py-3 text-sm text-info-foreground"
          >
            Проверяем, нет ли незавершённого электронного возврата…
          </p>
        ) : null}

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
              onClick={() => setCancelAttemptOpen(true)}
              isLoading={attemptBusy}
            >
              Отменить заявку
            </Button>
          ) : null}
          {recoveryBlocked && pendingOperationRef.current ? (
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
          {recoveryBlocked && !pendingOperationRef.current && activeRefundAttempt.isError ? (
            <Button
              variant="secondary"
              onClick={() => {
                serverRecoveryHandledRef.current = false;
                void activeRefundAttempt.refetch();
              }}
              isLoading={activeRefundAttempt.isFetching}
            >
              Повторить поиск
            </Button>
          ) : null}
          <Button
            onClick={() => void onSubmit()}
            isLoading={
              refund.isPending || reconciling || attemptBusy || activeAttemptLookupInProgress
            }
            disabled={
              !reasonMode ||
              settings.isLoading ||
              recoveryBlocked ||
              reconciling ||
              attemptBusy ||
              activeAttemptLookupInProgress ||
              refund.isPending ||
              selectedSummary.count === 0 ||
              ((refundAttempt?.status === "pending" ||
                refundAttempt?.status === "requires_reconciliation") &&
                !canConfirmExternal)
            }
          >
            {buttonLabel}
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={cancelAttemptOpen}
        title="Отменить заявку возврата?"
        message="Подтвердите только если деньги не были возвращены через терминал или QR. Если возврат мог пройти, сначала закройте это окно и проверьте документ терминала."
        confirmLabel="Деньги не возвращены — отменить"
        cancelLabel="Продолжить проверку"
        variant="danger"
        isLoading={attemptBusy}
        onCancel={() => setCancelAttemptOpen(false)}
        onConfirm={() => void cancelPendingAttempt()}
      />
    </Modal>
  );
}

function formatRefundDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return refundDateFormatter.format(new Date(year, month - 1, day, 12));
}

function formatRefundMoney(value: number): string {
  return value.toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}
