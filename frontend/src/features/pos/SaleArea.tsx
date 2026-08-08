import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { Suspense, lazy, useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Button, ConfirmDialog } from "@/components/ui";
import { findByBarcode } from "@/features/catalog/api";
import { requestDesktopCashDrawerOpen } from "@/lib/desktopBridge";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { BarcodeListener } from "./BarcodeListener";
import { CartList } from "./CartList";
import { PaymentPanel } from "./PaymentPanel";
import { PrescriptionModal } from "./PrescriptionModal";
import { QuickProducts } from "./QuickProducts";
import { ReceiptPrintModal } from "./ReceiptPrintModal";
import { SearchBar } from "./SearchBar";
import { ShiftBar } from "./ShiftBar";
import { getCheckoutResult } from "./api";
import { beep } from "./beep";
import {
  clearPendingCheckoutOperation,
  createPendingCheckoutOperation,
  loadPendingCheckoutOperation,
  type PendingCheckoutOperation,
} from "./checkoutOperation";
import {
  clearPendingCompletion,
  hasPendingCompletion,
  markPendingCompletion,
} from "./completionOperation";
import {
  type DraftInit,
  clearDraft as clearDraftStorage,
  loadDraft,
  saveDraft,
} from "./draftStorage";
import {
  mergeCheckoutResult,
  posKeys,
  useAddPayment,
  useAddSaleItem,
  useAddPrescription,
  useCheckoutSale,
  useCompleteSale,
  useCreateSale,
  useCurrentShiftQuery,
  useDeleteSaleItem,
  useSaleQuery,
  useUpdateSaleItem,
} from "./queries";
import {
  clearPendingPaymentOperation,
  createPendingPaymentOperation,
  loadPendingPaymentOperation,
  type PendingPaymentOperation,
} from "./paymentOperation";
import {
  type Payment,
  type PaymentMethod,
  type PaymentMethodRead,
  type PrescriptionLogPayload,
  type SaleCheckoutResult,
  type SaleDetails,
} from "./types";
import { type PosMode } from "./usePosMode";

// Lazy so the on-screen keypad chunk only loads when a cashier taps a field.
const NumPad = lazy(() => import("./NumPad"));

type NumPadState =
  | { kind: "qty"; itemId: string; initial: string }
  | { kind: "payment"; method: PaymentMethod; initial: string };

type FlashTone = "success" | "danger";
type CheckoutRecoveryOutcome = "resolved" | "not-found" | "unavailable" | "conflict";
type StagedPayment = Omit<Payment, "payment_method"> & { payment_method: PaymentMethod };

function isDefiniteRejection(error: unknown): boolean {
  if (!isAxiosError(error) || error.response === undefined) return false;
  const status = error.response.status;
  return status >= 400 && status < 500 && status !== 408 && status !== 409;
}

type PaymentReconciliation = "pending" | "matched" | "conflict" | "settled-elsewhere";

function reconcilePayment(
  sale: SaleDetails,
  operation: PendingPaymentOperation,
): PaymentReconciliation {
  const recorded = sale.payments.find((payment) => payment.operation_id === operation.operationId);
  if (recorded) {
    return recorded.payment_method === operation.paymentMethod &&
      Number(recorded.amount).toFixed(2) === operation.amount
      ? "matched"
      : "conflict";
  }

  const paid = sale.payments.reduce((sum, payment) => sum + Number(payment.amount), 0);
  return paid + 0.001 >= Number(sale.total_amount) ? "settled-elsewhere" : "pending";
}

function checkoutItemsFrom(items: SaleDetails["items"]): { catalog_id: string; qty: string }[] {
  const quantityByCatalog = new Map<string, number>();
  for (const item of items) {
    const thousandths = Math.round(Number(item.qty) * 1000);
    if (!Number.isSafeInteger(thousandths) || thousandths <= 0) {
      throw new Error("Invalid checkout quantity");
    }
    quantityByCatalog.set(
      item.catalog_id,
      (quantityByCatalog.get(item.catalog_id) ?? 0) + thousandths,
    );
  }
  return Array.from(quantityByCatalog, ([catalog_id, thousandths]) => ({
    catalog_id,
    qty: (thousandths / 1000).toFixed(3),
  }));
}

/**
 * The POS workspace. Owns the shift gate and the active sale, and lays the UI
 * out responsively: primary search across the workspace, cart and payment
 * columns on wide screens, and a single stack below that. `mode` decides
 * touch- vs keyboard-optimised behaviour.
 */
export function SaleArea({
  registerId,
  mode,
  soundOn,
  draftTtlMin,
  paymentMethods,
  mixedPaymentEnabled,
  paymentSettingsLoading,
  paymentSettingsUnavailable,
  canOpenShift = true,
  canCloseShift = true,
  canSell = true,
  workstationControls,
}: {
  registerId: string;
  mode: PosMode;
  soundOn: boolean;
  draftTtlMin: number;
  paymentMethods: PaymentMethod[];
  mixedPaymentEnabled: boolean;
  paymentSettingsLoading: boolean;
  paymentSettingsUnavailable: boolean;
  canOpenShift?: boolean;
  canCloseShift?: boolean;
  canSell?: boolean;
  workstationControls?: ReactNode;
}): JSX.Element {
  const shiftQuery = useCurrentShiftQuery(registerId);
  const hasShift = Boolean(shiftQuery.data);

  return (
    <div className="min-w-0">
      {hasShift && canSell ? (
        // Key by register so switching registers restores that one's draft.
        <ActiveWorkspace
          key={registerId}
          registerId={registerId}
          branchId={shiftQuery.data?.branch_id ?? null}
          mode={mode}
          soundOn={soundOn}
          draftTtlMin={draftTtlMin}
          paymentMethods={paymentMethods}
          mixedPaymentEnabled={mixedPaymentEnabled}
          paymentSettingsLoading={paymentSettingsLoading}
          paymentSettingsUnavailable={paymentSettingsUnavailable}
          canCloseShift={canCloseShift}
          workstationControls={workstationControls}
        />
      ) : (
        <div className="grid min-w-0 gap-3 xl:grid-cols-[auto_minmax(0,1fr)]">
          {workstationControls ? (
            <div className="flex min-w-0 items-center rounded-lg border border-border bg-surface px-3 py-2">
              {workstationControls}
            </div>
          ) : null}
          <ShiftBar
            registerId={registerId}
            mode={mode}
            canOpen={canOpenShift}
            canClose={canCloseShift}
          />
        </div>
      )}
    </div>
  );
}

function ActiveWorkspace({
  registerId,
  branchId,
  mode,
  soundOn,
  draftTtlMin,
  paymentMethods,
  mixedPaymentEnabled,
  paymentSettingsLoading,
  paymentSettingsUnavailable,
  canCloseShift,
  workstationControls,
}: {
  registerId: string;
  branchId: string | null;
  mode: PosMode;
  soundOn: boolean;
  draftTtlMin: number;
  paymentMethods: PaymentMethod[];
  mixedPaymentEnabled: boolean;
  paymentSettingsLoading: boolean;
  paymentSettingsUnavailable: boolean;
  canCloseShift: boolean;
  workstationControls?: ReactNode;
}): JSX.Element {
  const touch = mode === "touch";
  const keyboard = mode === "keyboard";
  const queryClient = useQueryClient();

  const [init] = useState<DraftInit>(() => loadDraft(registerId, draftTtlMin));
  const [saleId, setSaleId] = useState<string | null>(init.saleId);
  const [nameById, setNameById] = useState<Record<string, string>>(init.nameById);
  const [pendingPayment, setPendingPayment] = useState<PendingPaymentOperation | null>(() =>
    init.saleId ? loadPendingPaymentOperation(init.saleId) : null,
  );
  const [pendingCheckout, setPendingCheckout] = useState<PendingCheckoutOperation | null>(() =>
    init.saleId ? loadPendingCheckoutOperation(init.saleId) : null,
  );
  const [completionUncertain, setCompletionUncertain] = useState(
    () => init.saleId !== null && hasPendingCompletion(init.saleId),
  );
  const [checkoutUncertain, setCheckoutUncertain] = useState(() => pendingCheckout !== null);
  const [checkoutReconciling, setCheckoutReconciling] = useState(false);
  const [staleNotice, setStaleNotice] = useState<boolean>(init.expired);
  const [topError, setTopError] = useState<string | null>(null);
  const [prescriptionOpen, setPrescriptionOpen] = useState(false);
  const [requiresRx, setRequiresRx] = useState(init.requiresRx);
  const [prescription, setPrescription] = useState<PrescriptionLogPayload | null>(null);
  const [stagedPayments, setStagedPayments] = useState<StagedPayment[]>([]);
  const [payingMethod, setPayingMethod] = useState<PaymentMethodRead | null>(null);
  const [numpad, setNumpad] = useState<NumPadState | null>(null);
  const [flash, setFlash] = useState<FlashTone | null>(null);
  const [printOpen, setPrintOpen] = useState(false);
  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false);
  const [paymentPanelVisible, setPaymentPanelVisible] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const paymentPanelRef = useRef<HTMLDivElement>(null);
  const flashTimer = useRef<number | undefined>(undefined);
  const completingRef = useRef(false);
  const checkoutRecoveryRef = useRef(false);
  const openedDrawerOperationRef = useRef<string | null>(null);
  const stagedPaymentSequenceRef = useRef(0);
  const saleIdRef = useRef<string | null>(saleId);
  const saleCreationRef = useRef<Promise<string> | null>(null);
  const scanQueueRef = useRef<Promise<void>>(Promise.resolve());
  saleIdRef.current = saleId;

  const createSale = useCreateSale();
  const addItem = useAddSaleItem();
  const updateItem = useUpdateSaleItem();
  const deleteItem = useDeleteSaleItem();
  const addPayment = useAddPayment();
  const addPrescription = useAddPrescription();
  const completeSale = useCompleteSale();
  const checkoutSale = useCheckoutSale();
  const saleQuery = useSaleQuery(saleId);
  const refetchSale = saleQuery.refetch;

  const sale: SaleDetails | null = saleQuery.data ?? null;
  const isDraft = !sale || sale.status === "draft";
  const items = sale?.items ?? [];
  const recordedPayments = sale?.payments ?? [];
  const payments = isDraft ? [...recordedPayments, ...stagedPayments] : recordedPayments;
  const currency = sale?.currency ?? "TJS";
  const totalDue = sale ? Number(sale.total_amount) : 0;
  const totalPaid = payments.reduce((sum, p) => sum + Number(p.amount), 0);
  const remaining = totalDue - totalPaid;
  const saleEditingBlocked =
    completeSale.isPending ||
    checkoutSale.isPending ||
    completionUncertain ||
    checkoutUncertain ||
    pendingCheckout !== null ||
    pendingPayment !== null ||
    (saleId !== null && sale === null);
  const paymentMethodsKey = paymentMethods.join("|");

  useEffect(() => {
    if (stagedPayments.length === 0) return;

    const hasUnavailableMethod = stagedPayments.some(
      (payment) => !paymentMethods.includes(payment.payment_method),
    );
    const stagedTotal = stagedPayments.reduce(
      (sum, payment) => sum + Number(payment.amount),
      0,
    );
    const violatesSingleMethod =
      !mixedPaymentEnabled &&
      (new Set(stagedPayments.map((payment) => payment.payment_method)).size > 1 ||
        stagedTotal + 0.001 < totalDue);

    if (!hasUnavailableMethod && !violatesSingleMethod) return;
    setStagedPayments([]);
    setTopError(
      "Настройки способов оплаты изменились. Выберите доступный способ оплаты заново.",
    );
  }, [
    mixedPaymentEnabled,
    paymentMethods,
    paymentMethodsKey,
    stagedPayments,
    totalDue,
  ]);

  const clearDraft = useCallback(() => clearDraftStorage(registerId), [registerId]);
  const persistCompletedReceipt = useCallback(
    (completedSaleId: string): boolean => {
      const persisted = saveDraft(registerId, completedSaleId, nameById, "completed", false);
      if (persisted) {
        clearPendingCompletion(completedSaleId);
        clearPendingCheckoutOperation(completedSaleId);
        setPendingCheckout(null);
        setCompletionUncertain(false);
        setCheckoutUncertain(false);
        setTopError(null);
        return true;
      }

      setCompletionUncertain(true);
      setCheckoutUncertain(true);
      setTopError(
        "Чек завершён, но не удалось сохранить ссылку для восстановления. Не закрывайте приложение и повторите сверку.",
      );
      return false;
    },
    [registerId, nameById],
  );

  const acceptCheckoutResult = useCallback(
    async (
      result: SaleCheckoutResult,
      operation: PendingCheckoutOperation,
      openCashDrawer: boolean,
    ): Promise<boolean> => {
      if (
        result.operation_id !== operation.operationId ||
        result.sale_id !== operation.saleId ||
        result.register_id !== operation.registerId ||
        result.sale_id !== saleId ||
        result.register_id !== registerId
      ) {
        setCheckoutUncertain(true);
        setTopError(
          "Результат операции не совпал с текущим чеком. Не повторяйте оплату и обратитесь к администратору.",
        );
        return false;
      }

      if (
        openCashDrawer &&
        result.payments.some((payment) => payment.payment_method === "cash") &&
        openedDrawerOperationRef.current !== result.operation_id
      ) {
        requestDesktopCashDrawerOpen({
          reason: "sale-completed",
          registerId,
          saleId: result.sale_id,
        });
        openedDrawerOperationRef.current = result.operation_id;
      }

      queryClient.setQueryData<SaleDetails>(posKeys.sale(result.sale_id), (current) =>
        mergeCheckoutResult(current, result),
      );
      persistCompletedReceipt(result.sale_id);
      setStagedPayments([]);
      setPrescription(null);
      setRequiresRx(false);
      await refetchSale();
      return true;
    },
    [persistCompletedReceipt, queryClient, refetchSale, registerId, saleId],
  );

  const recoverCheckout = useCallback(
    async (
      operation: PendingCheckoutOperation,
      openCashDrawer: boolean,
    ): Promise<CheckoutRecoveryOutcome> => {
      if (checkoutRecoveryRef.current) return "unavailable";
      checkoutRecoveryRef.current = true;
      setCheckoutReconciling(true);
      try {
        const result = await getCheckoutResult(operation.operationId);
        const accepted = await acceptCheckoutResult(result, operation, openCashDrawer);
        return accepted ? "resolved" : "conflict";
      } catch (error) {
        if (isAxiosError(error) && error.response?.status === 404) {
          clearPendingCheckoutOperation(operation.saleId, operation.operationId);
          setPendingCheckout((current) =>
            current?.operationId === operation.operationId ? null : current,
          );
          setCheckoutUncertain(false);
          setTopError("Операция не была проведена. Проверьте соединение и повторите оплату.");
          return "not-found";
        }

        setCheckoutUncertain(true);
        setTopError(
          "Не удалось проверить результат продажи. Не повторяйте оплату, пока сверка с сервером не завершится.",
        );
        return "unavailable";
      } finally {
        checkoutRecoveryRef.current = false;
        setCheckoutReconciling(false);
      }
    },
    [acceptCheckoutResult],
  );

  // Persist the live draft so a reload (or accidental close) restores the cart.
  // The savedAt stamp refreshes on every change → the TTL is an idle timeout.
  useEffect(() => {
    if (sale?.status === "draft") {
      saveDraft(registerId, sale.id, nameById, "draft", requiresRx);
    }
  }, [sale, nameById, registerId, requiresRx]);

  // Keep the completed receipt addressable until the cashier explicitly starts
  // another sale. This makes printer/browser recovery deterministic.
  useEffect(() => {
    if (sale && sale.status !== "draft") {
      persistCompletedReceipt(sale.id);
    }
  }, [sale, persistCompletedReceipt]);

  useEffect(() => {
    if (
      !pendingCheckout ||
      (!sale && !saleQuery.isError) ||
      saleQuery.isFetching ||
      checkoutSale.isPending ||
      completingRef.current
    ) {
      return;
    }
    void recoverCheckout(pendingCheckout, false);
  }, [
    checkoutSale.isPending,
    pendingCheckout,
    recoverCheckout,
    sale,
    saleQuery.isError,
    saleQuery.isFetching,
  ]);

  useEffect(() => {
    if (!pendingPayment || !sale) return;
    const result = reconcilePayment(sale, pendingPayment);
    if (result === "pending" && sale.status === "draft") return;

    clearPendingPaymentOperation(pendingPayment.saleId, pendingPayment.operationId);
    setPendingPayment(null);
    if (result === "matched") {
      setTopError(null);
    } else if (result === "conflict") {
      setTopError("Параметры сохранённой оплаты не совпали с сервером. Проверьте оплаты чека.");
    } else if (result === "settled-elsewhere") {
      setTopError("Чек уже оплачен другой операцией. Проверьте оплаты перед завершением.");
    }
  }, [pendingPayment, sale]);

  useEffect(() => {
    if (!saleId || (!pendingPayment && !completionUncertain)) return;
    if (saleQuery.isError) {
      setTopError(
        (current) =>
          current ??
          "Не удалось загрузить чек для сверки денежной операции. Проверьте соединение и повторите сверку.",
      );
      return;
    }
    if (!sale) return;
    if (
      pendingPayment &&
      !addPayment.isPending &&
      sale.status === "draft" &&
      reconcilePayment(sale, pendingPayment) === "pending"
    ) {
      setTopError((current) =>
        current === null || current.startsWith("Не удалось загрузить чек для сверки")
          ? "Результат предыдущей оплаты не подтверждён. Повторите оплату тем же способом или выполните сверку."
          : current,
      );
    } else if (completionUncertain && !completeSale.isPending && sale.status === "draft") {
      setTopError((current) =>
        current === null || current.startsWith("Не удалось загрузить чек для сверки")
          ? "Результат завершения продажи не подтверждён. Повторите завершение или выполните сверку с сервером."
          : current,
      );
    }
  }, [
    saleId,
    sale,
    saleQuery.isError,
    pendingPayment,
    completionUncertain,
    addPayment.isPending,
    completeSale.isPending,
  ]);

  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  useEffect(() => {
    const panel = paymentPanelRef.current;
    if (!panel || typeof IntersectionObserver === "undefined") return undefined;

    const observer = new IntersectionObserver(
      ([entry]) =>
        setPaymentPanelVisible(entry?.isIntersecting === true && entry.intersectionRatio >= 0.35),
      { threshold: 0.35 },
    );
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);

  const doFlash = (tone: FlashTone) => {
    setFlash(tone);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(null), 600);
  };

  // Lazily create a draft on the first add so we never leave empty drafts.
  const ensureSaleId = useCallback(async (): Promise<string> => {
    if (saleIdRef.current) return saleIdRef.current;
    if (saleCreationRef.current) return saleCreationRef.current;

    const creation = createSale.mutateAsync(registerId).then((created) => {
      saleIdRef.current = created.id;
      setSaleId(created.id);
      setStaleNotice(false);
      return created.id;
    });
    saleCreationRef.current = creation;
    try {
      return await creation;
    } finally {
      if (saleCreationRef.current === creation) saleCreationRef.current = null;
    }
  }, [createSale, registerId]);

  const onAdd = async (catalogId: string, name: string, qty: number): Promise<boolean> => {
    if (saleEditingBlocked) return false;
    setTopError(null);
    try {
      const id = await ensureSaleId();
      if (name) setNameById((m) => ({ ...m, [catalogId]: name }));
      const res = await addItem.mutateAsync({ saleId: id, catalogId, qty: String(qty) });
      setStagedPayments([]);
      if (res.requires_prescription_log) {
        setRequiresRx(true);
        setPrescriptionOpen(true);
      }
      searchRef.current?.focus();
      return true;
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось добавить позицию"));
      return false;
    }
  };

  const onScan = async (code: string) => {
    setTopError(null);
    try {
      const item = await findByBarcode(code);
      const added = await onAdd(item.id, item.brand_name, 1);
      if (!added) {
        doFlash("danger");
        return;
      }
      doFlash("success");
      if (soundOn) beep();
    } catch (err) {
      doFlash("danger");
      if (isAxiosError(err) && err.response?.status === 404) {
        setTopError(`Штрихкод ${code} не найден`);
      } else {
        setTopError(describeApiError(err, `Штрихкод ${code} не найден`));
      }
    }
  };

  const enqueueScan = (code: string) => {
    scanQueueRef.current = scanQueueRef.current.then(
      () => onScan(code),
      () => onScan(code),
    );
  };

  const onQtyChange = async (itemId: string, qty: number) => {
    if (!saleId || saleEditingBlocked) return;
    setTopError(null);
    try {
      await updateItem.mutateAsync({ saleId, itemId, qty: String(qty) });
      setStagedPayments([]);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось изменить количество"));
    }
  };

  const onDelete = async (itemId: string) => {
    if (!saleId || saleEditingBlocked) return;
    try {
      await deleteItem.mutateAsync({ saleId, itemId });
      setStagedPayments([]);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось удалить"));
    }
  };

  const payLegacy = async (method: PaymentMethodRead, amount: string) => {
    if (!saleId) return;
    const amt = Number(amount);
    if (!(amt > 0)) return;
    const normalizedAmount = amt.toFixed(2);
    const storedOperation =
      pendingPayment?.saleId === saleId ? pendingPayment : loadPendingPaymentOperation(saleId);
    if (
      !storedOperation &&
      (method === "bank_transfer" || !paymentMethods.includes(method))
    ) {
      setTopError("Этот способ оплаты больше не доступен для новых операций.");
      return;
    }
    const recordedMethod = recordedPayments[0]?.payment_method;
    if (
      !mixedPaymentEnabled &&
      recordedMethod !== undefined &&
      recordedMethod !== method
    ) {
      setTopError("Смешанная оплата отключена. Продолжите оплату тем же способом.");
      return;
    }
    if (
      !mixedPaymentEnabled &&
      recordedMethod === undefined &&
      !storedOperation &&
      amt + 0.001 < remaining
    ) {
      setTopError("Смешанная оплата отключена. Внесите всю оставшуюся сумму одним способом.");
      return;
    }
    if (!storedOperation && amt - remaining > 0.001) {
      setTopError(`Сумма оплаты превышает остаток ${remaining.toFixed(2)} ${currency}.`);
      return;
    }
    if (
      storedOperation &&
      (storedOperation.paymentMethod !== method || storedOperation.amount !== normalizedAmount)
    ) {
      setPendingPayment(storedOperation);
      setTopError("Результат предыдущей оплаты ещё не подтверждён. Повторите её тем же способом.");
      return;
    }

    if (!saveDraft(registerId, saleId, nameById, "draft", requiresRx)) {
      setTopError(
        "Локальное хранилище кассы недоступно. Оплата не отправлена; освободите место или перезапустите приложение.",
      );
      return;
    }
    const operation =
      storedOperation ??
      (method === "bank_transfer"
        ? null
        : createPendingPaymentOperation(saleId, method, normalizedAmount));
    if (!operation) {
      setTopError(
        "Не удалось сохранить ключ безопасного повтора. Оплата не отправлена; перезапустите приложение.",
      );
      return;
    }
    setPendingPayment(operation);
    setTopError(null);
    setPayingMethod(method);
    try {
      await addPayment.mutateAsync({
        saleId,
        payload: {
          operation_id: operation.operationId,
          payment_method: method,
          amount: normalizedAmount,
        },
      });
      clearPendingPaymentOperation(saleId, operation.operationId);
      setPendingPayment(null);
    } catch (err) {
      if (isDefiniteRejection(err)) {
        clearPendingPaymentOperation(saleId, operation.operationId);
        setPendingPayment(null);
        setTopError(describeApiError(err, "Не удалось добавить оплату"));
      } else {
        const refreshed = await saleQuery.refetch();
        const reconciliation = refreshed.data
          ? reconcilePayment(refreshed.data, operation)
          : "pending";
        if (reconciliation !== "pending") {
          clearPendingPaymentOperation(saleId, operation.operationId);
          setPendingPayment(null);
          if (reconciliation === "matched") {
            setTopError(null);
          } else if (reconciliation === "conflict") {
            setTopError(
              "Параметры сохранённой оплаты не совпали с сервером. Проверьте оплаты чека.",
            );
          } else {
            setTopError("Чек уже оплачен другой операцией. Проверьте оплаты перед завершением.");
          }
        } else {
          setTopError(
            "Не удалось подтвердить результат оплаты. Проверьте соединение и повторите оплату тем же способом.",
          );
        }
      }
    } finally {
      setPayingMethod(null);
    }
  };

  const stagePayment = (method: PaymentMethod, amount: string) => {
    if (!saleId || recordedPayments.length > 0) return;
    if (!paymentMethods.includes(method)) {
      setTopError("Этот способ оплаты отключён в настройках аптеки.");
      return;
    }
    const numericAmount = Number(amount);
    if (!(numericAmount > 0)) return;
    if (
      !mixedPaymentEnabled &&
      (stagedPayments.length > 0 || numericAmount + 0.001 < remaining)
    ) {
      setTopError("Смешанная оплата отключена. Внесите всю сумму одним способом.");
      return;
    }
    if (numericAmount - remaining > 0.001) {
      setTopError(
        `Сумма оплаты превышает остаток ${Math.max(0, remaining).toFixed(2)} ${currency}.`,
      );
      return;
    }

    stagedPaymentSequenceRef.current += 1;
    setStagedPayments((current) => [
      ...current,
      {
        id: `staged-${stagedPaymentSequenceRef.current}`,
        sale_id: saleId,
        operation_id: null,
        payment_method: method,
        amount: numericAmount.toFixed(2),
        currency,
      },
    ]);
    setTopError(null);
  };

  const submitPayment = (method: PaymentMethod, amount: string) => {
    if (recordedPayments.length > 0 || pendingPayment !== null) {
      void payLegacy(method, amount);
      return;
    }
    stagePayment(method, amount);
  };

  // Touch: tapping a tile opens the keypad pre-filled with the remaining amount
  // (so partial payments are easy). Keyboard/desktop: one tap pays the rest.
  const onPayTile = (method: PaymentMethod, requestedAmount?: string) => {
    if (
      !saleId ||
      remaining <= 0.001 ||
      paymentSettingsLoading ||
      paymentSettingsUnavailable ||
      !paymentMethods.includes(method)
    ) {
      return;
    }
    if (
      completionUncertain ||
      checkoutUncertain ||
      completeSale.isPending ||
      checkoutSale.isPending ||
      checkoutReconciling
    ) {
      return;
    }
    if (pendingPayment) {
      if (pendingPayment.paymentMethod !== method) {
        setTopError(
          "Результат предыдущей оплаты ещё не подтверждён. Повторите её тем же способом.",
        );
        return;
      }
      void payLegacy(method, pendingPayment.amount);
      return;
    }
    if (requestedAmount !== undefined) {
      const amount = Math.min(Number(requestedAmount), remaining);
      if (Number.isFinite(amount) && amount > 0) {
        submitPayment(method, amount.toFixed(2));
      }
      return;
    }
    if (touch) {
      setNumpad({ kind: "payment", method, initial: remaining.toFixed(2) });
      return;
    }
    submitPayment(method, remaining.toFixed(2));
  };

  const completeLegacySale = async () => {
    if (!saleId || completingRef.current || completeSale.isPending) return;
    if (pendingPayment || addPayment.isPending) {
      setTopError("Сначала подтвердите результат оплаты.");
      return;
    }
    if (requiresRx && !prescription) {
      setPrescriptionOpen(true);
      return;
    }
    if (remaining > 0.001) {
      setTopError(`Осталось оплатить ${remaining.toFixed(2)} ${currency}`);
      return;
    }
    if (!saveDraft(registerId, saleId, nameById, "draft", requiresRx)) {
      setTopError(
        "Локальное хранилище кассы недоступно. Завершение не отправлено; освободите место или перезапустите приложение.",
      );
      return;
    }
    if (!markPendingCompletion(saleId)) {
      setTopError(
        "Не удалось сохранить маркер восстановления. Завершение не отправлено; перезапустите приложение.",
      );
      return;
    }
    setTopError(null);
    completingRef.current = true;
    try {
      if (prescription) {
        await addPrescription.mutateAsync({ saleId, payload: prescription });
        setPrescription(null);
        setRequiresRx(false);
      }
      await completeSale.mutateAsync(saleId);
      if (payments.some((payment) => payment.payment_method === "cash")) {
        requestDesktopCashDrawerOpen({
          reason: "sale-completed",
          registerId,
          saleId,
        });
      }
      persistCompletedReceipt(saleId);
      completingRef.current = false;
    } catch (err) {
      if (!isDefiniteRejection(err)) {
        const refreshed = await saleQuery.refetch();
        if (refreshed.data?.status === "completed") {
          if (payments.some((payment) => payment.payment_method === "cash")) {
            requestDesktopCashDrawerOpen({
              reason: "sale-completed",
              registerId,
              saleId,
            });
          }
          persistCompletedReceipt(saleId);
          completingRef.current = false;
          return;
        }
        setCompletionUncertain(true);
        setTopError(
          "Не удалось подтвердить завершение продажи. Проверьте соединение и повторите завершение.",
        );
      } else {
        clearPendingCompletion(saleId);
        setCompletionUncertain(false);
        setTopError(describeApiError(err, "Не удалось завершить продажу"));
      }
      completingRef.current = false;
    }
  };

  const onComplete = async () => {
    if (!saleId || !sale || completingRef.current) return;
    if (recordedPayments.length > 0 || pendingPayment || completionUncertain) {
      await completeLegacySale();
      return;
    }
    if (checkoutSale.isPending || checkoutReconciling) return;
    if (requiresRx && !prescription) {
      setPrescriptionOpen(true);
      return;
    }
    if (remaining > 0.001) {
      setTopError(`Осталось оплатить ${remaining.toFixed(2)} ${currency}`);
      return;
    }
    if (stagedPayments.length === 0) {
      setTopError("Добавьте оплату перед завершением продажи.");
      return;
    }

    const storedOperation = pendingCheckout ?? loadPendingCheckoutOperation(saleId);
    if (storedOperation) {
      setPendingCheckout(storedOperation);
      setCheckoutUncertain(true);
      await recoverCheckout(storedOperation, true);
      return;
    }

    let checkoutItems: { catalog_id: string; qty: string }[];
    try {
      checkoutItems = checkoutItemsFrom(items);
    } catch {
      setTopError("Не удалось проверить количество товаров в чеке.");
      return;
    }
    if (checkoutItems.length === 0) {
      setTopError("Добавьте хотя бы один товар.");
      return;
    }
    if (!saveDraft(registerId, saleId, nameById, "draft", requiresRx)) {
      setTopError(
        "Локальное хранилище кассы недоступно. Продажа не отправлена; освободите место или перезапустите приложение.",
      );
      return;
    }

    const operation = createPendingCheckoutOperation(saleId, registerId);
    if (!operation) {
      setTopError(
        "Не удалось сохранить маркер восстановления. Продажа не отправлена; перезапустите приложение.",
      );
      return;
    }

    setPendingCheckout(operation);
    setCheckoutUncertain(false);
    setTopError(null);
    completingRef.current = true;
    try {
      const result = await checkoutSale.mutateAsync({
        operation_id: operation.operationId,
        register_id: registerId,
        draft_sale_id: saleId,
        items: checkoutItems,
        payments: stagedPayments.map((payment) => ({
          payment_method: payment.payment_method,
          amount: payment.amount,
        })),
        prescription: prescription
          ? {
              prescription_number: prescription.prescription_number,
              doctor_name: prescription.doctor_name,
              doctor_license: prescription.doctor_license,
              patient_name: prescription.patient_name,
              notes: prescription.notes,
            }
          : undefined,
      });
      await acceptCheckoutResult(result, operation, true);
    } catch (error) {
      if (isDefiniteRejection(error)) {
        clearPendingCheckoutOperation(saleId, operation.operationId);
        setPendingCheckout(null);
        setCheckoutUncertain(false);
        setTopError(describeApiError(error, "Не удалось оформить продажу"));
      } else {
        setCheckoutUncertain(true);
        const outcome = await recoverCheckout(operation, true);
        if (outcome === "not-found") {
          setTopError(describeApiError(error, "Продажа не была оформлена. Повторите оплату."));
        }
      }
    } finally {
      completingRef.current = false;
    }
  };

  const startNewSale = (): boolean => {
    if (!clearDraft()) {
      setTopError(
        "Не удалось очистить локальное состояние кассы. Новая продажа не начата; перезапустите приложение.",
      );
      return false;
    }
    completingRef.current = false;
    if (saleId) {
      clearPendingPaymentOperation(saleId);
      clearPendingCompletion(saleId);
      clearPendingCheckoutOperation(saleId);
    }
    saleIdRef.current = null;
    setSaleId(null);
    setNameById({});
    setRequiresRx(false);
    setPrescription(null);
    setStagedPayments([]);
    setPendingCheckout(null);
    setCheckoutUncertain(false);
    setTopError(null);
    setStaleNotice(false);
    return true;
  };

  const onNewSale = (): boolean => {
    if (
      completingRef.current ||
      completeSale.isPending ||
      checkoutSale.isPending ||
      checkoutReconciling ||
      addPayment.isPending ||
      pendingPayment ||
      completionUncertain ||
      pendingCheckout ||
      checkoutUncertain
    ) {
      setTopError("Сначала подтвердите результат текущей денежной операции.");
      return false;
    }
    if (isDraft && items.length > 0) {
      setDiscardConfirmOpen(true);
      return false;
    }
    return startNewSale();
  };

  const onQtyTap = (itemId: string) => {
    const it = items.find((i) => i.id === itemId);
    if (!it) return;
    setNumpad({ kind: "qty", itemId, initial: String(Number(it.qty)) });
  };

  const onNumpadSubmit = (value: string) => {
    if (!numpad) return;
    if (numpad.kind === "qty") {
      const q = Math.max(1, Math.round(Number(value)));
      if (Number.isFinite(q)) void onQtyChange(numpad.itemId, q);
    } else {
      submitPayment(numpad.method, value);
    }
    setNumpad(null);
  };

  // Enter on a ready receipt uses the first owner-enabled payment method.
  // The handler below guards it so it never fires while typing in a field or
  // with a dialog open.
  const onEnterPayDefault = () => {
    const defaultMethod = paymentMethods[0];
    if (
      defaultMethod &&
      !paymentSettingsLoading &&
      !paymentSettingsUnavailable &&
      isDraft &&
      totalDue > 0 &&
      remaining > 0.001
    ) {
      onPayTile(defaultMethod);
    }
  };

  // Physical keyboard shortcuts stay available on touch-capable laptops.
  // Ref holds the latest handlers so we bind the listener once. F-keys are
  // ignored while any modal/keypad (role="dialog") is open.
  const actionsRef = useRef({
    onNewSale,
    onComplete,
    onEnterPayDefault,
    canDismissError: !saleEditingBlocked,
  });
  actionsRef.current = {
    onNewSale,
    onComplete,
    onEnterPayDefault,
    canDismissError: !saleEditingBlocked,
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const dialogOpen = document.querySelector('[role="dialog"]') !== null;
      const el = document.activeElement as HTMLElement | null;
      const typing =
        !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      // A field holding text owns its own Enter (qty → add, search → typing).
      // An empty field (or no field) lets Enter fall through to "pay cash".
      const fieldHasContent =
        !!el &&
        (el.tagName === "INPUT" || el.tagName === "TEXTAREA") &&
        ((el as HTMLInputElement).value ?? "").trim() !== "";
      const interactive =
        !!el &&
        el.closest(
          "button, a[href], select, [role='button'], [role='menuitem'], [role='option']",
        ) !== null;
      switch (e.key) {
        case "F2":
          if (dialogOpen) return;
          e.preventDefault();
          if (actionsRef.current.onNewSale()) searchRef.current?.focus();
          break;
        case "F3":
          if (dialogOpen) return;
          e.preventDefault();
          document.querySelector<HTMLElement>(".pos-tile")?.focus();
          break;
        case "F4":
          if (dialogOpen) return;
          e.preventDefault();
          void actionsRef.current.onComplete();
          break;
        case "/":
          if (dialogOpen || typing) return;
          e.preventDefault();
          searchRef.current?.focus();
          break;
        case "Enter":
          if (dialogOpen || fieldHasContent || interactive) return;
          e.preventDefault();
          actionsRef.current.onEnterPayDefault();
          break;
        case "Escape":
          if (!dialogOpen && actionsRef.current.canDismissError) setTopError(null);
          break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <>
      {/* Global barcode capture — only while editing a draft. */}
      <BarcodeListener enabled={isDraft && !saleEditingBlocked} onScan={enqueueScan} />

      {/* Scan feedback: green/red edge flash (collapses to a static border under
          reduced-motion). */}
      {flash && (
        <div
          aria-hidden="true"
          className={cn(
            "pointer-events-none fixed inset-0 z-toast ring-4 ring-inset transition-opacity duration-slow",
            flash === "success" ? "ring-success" : "ring-danger",
          )}
        />
      )}

      <div className="space-y-3">
        <div className="grid min-w-0 gap-3 xl:grid-cols-[auto_minmax(0,1fr)]">
          {workstationControls ? (
            <div className="flex min-w-0 items-center rounded-lg border border-border bg-surface px-3 py-2">
              {workstationControls}
            </div>
          ) : null}
          <ShiftBar
            registerId={registerId}
            mode={mode}
            canClose={canCloseShift}
            closeBlocked={saleEditingBlocked || (isDraft && items.length > 0)}
          />
        </div>

        {staleNotice && (
          <p className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
            Прошлый черновик устарел (более {draftTtlMin} мин) и был очищен — начните новую продажу.
          </p>
        )}

        {isDraft && (
          <fieldset disabled={saleEditingBlocked} className="min-w-0 border-0 p-0">
            <SearchBar
              ref={searchRef}
              onAdd={onAdd}
              busy={createSale.isPending || addItem.isPending}
              touch={touch}
              branchId={branchId ?? undefined}
            />
          </fieldset>
        )}

        <div className="grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-12 xl:grid-cols-[minmax(18rem,1.15fr)_minmax(22rem,1fr)_minmax(18rem,0.78fr)]">
          <div className="min-w-0 lg:col-span-5 xl:col-auto">
            <QuickProducts
              branchId={branchId ?? undefined}
              onAdd={onAdd}
              busy={!isDraft || saleEditingBlocked || createSale.isPending || addItem.isPending}
              touch={touch}
            />
          </div>

          <section
            aria-labelledby="current-receipt-title"
            className={cn(
              "flex min-h-[30rem] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-surface lg:col-span-7 xl:col-auto",
              "xl:h-[36rem]",
              isDraft && totalDue > 0 && "mb-20 xl:mb-0",
            )}
          >
            <header className="flex min-h-14 items-center justify-between gap-3 border-b border-border px-4">
              <div className="min-w-0">
                <h2
                  id="current-receipt-title"
                  className="truncate text-base font-semibold text-foreground"
                >
                  Текущий чек
                  {sale?.receipt_number ? (
                    <span className="ml-2 font-mono text-xs font-normal text-foreground-muted">
                      № {sale.receipt_number}
                    </span>
                  ) : null}
                </h2>
                <p className="text-xs text-foreground-muted">
                  {items.length === 0
                    ? "Нет товаров"
                    : `${items.length} ${productCountLabel(items.length)}`}
                </p>
              </div>
              {requiresRx && !prescription ? (
                <span className="rounded-full bg-warning-subtle px-2.5 py-0.5 text-xs font-medium text-warning-foreground ring-1 ring-inset ring-warning/30">
                  требуется рецепт
                </span>
              ) : null}
            </header>

            <div className="hidden grid-cols-[minmax(0,1fr)_8.5rem_5.5rem_2.5rem] gap-2 border-b border-border bg-background px-4 py-2 text-xs font-medium text-foreground-muted sm:grid">
              <span>Товар</span>
              <span className="text-center">Количество</span>
              <span className="text-right">Сумма</span>
              <span aria-hidden="true" />
            </div>

            <CartList
              items={items}
              nameById={nameById}
              currency={currency}
              editable={isDraft && !saleEditingBlocked}
              onQtyChange={(id, q) => void onQtyChange(id, q)}
              onDelete={(id) => void onDelete(id)}
              onQtyTap={touch ? onQtyTap : undefined}
              touch={touch}
              busy={updateItem.isPending || deleteItem.isPending}
              embedded
            />

            {topError ? (
              <div className="flex flex-wrap items-center gap-2 border-t border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground">
                <p>{topError}</p>
                {pendingPayment || completionUncertain || pendingCheckout || checkoutUncertain ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    isLoading={saleQuery.isFetching || checkoutReconciling}
                    onClick={() => {
                      if (pendingCheckout) {
                        void recoverCheckout(pendingCheckout, false);
                      } else {
                        void saleQuery.refetch();
                      }
                    }}
                  >
                    Сверить с сервером
                  </Button>
                ) : null}
              </div>
            ) : null}

            <footer className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-t border-border bg-background px-3 py-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="text-danger hover:text-danger"
                disabled={!isDraft || items.length === 0 || saleEditingBlocked}
                onClick={onNewSale}
              >
                <ClearReceiptIcon />
                Очистить чек
              </Button>
              <div className="ml-auto flex items-baseline gap-4">
                <span className="text-xs text-foreground-muted">
                  {items.length} {productCountLabel(items.length)}
                </span>
                <strong className="font-mono text-xl tabular-nums text-foreground">
                  {totalDue.toFixed(2)}{" "}
                  <span className="font-sans text-xs font-semibold text-foreground-secondary">
                    {currency}
                  </span>
                </strong>
              </div>
            </footer>
          </section>

          <div
            ref={paymentPanelRef}
            className="min-w-0 scroll-mt-20 pb-20 lg:col-span-12 lg:pb-0 xl:col-auto"
          >
            <div className="xl:sticky xl:top-[calc(var(--app-header-height)+0.75rem)]">
              <PaymentPanel
                totalDue={totalDue}
                totalPaid={totalPaid}
                remaining={remaining}
                currency={currency}
                payments={payments}
                isDraft={isDraft}
                completing={completeSale.isPending || checkoutSale.isPending || checkoutReconciling}
                completionUncertain={completionUncertain || checkoutUncertain}
                payingMethod={payingMethod}
                pendingPaymentMethod={pendingPayment?.paymentMethod ?? null}
                paymentMethods={paymentMethods}
                mixedPaymentEnabled={mixedPaymentEnabled}
                paymentSettingsLoading={paymentSettingsLoading}
                paymentSettingsUnavailable={paymentSettingsUnavailable}
                onPayTile={onPayTile}
                onRetryPendingPayment={
                  pendingPayment
                    ? () => void payLegacy(pendingPayment.paymentMethod, pendingPayment.amount)
                    : undefined
                }
                onClearPayments={
                  stagedPayments.length > 0 && recordedPayments.length === 0
                    ? () => setStagedPayments([])
                    : undefined
                }
                onComplete={() => void onComplete()}
                completedReceiptNumber={!isDraft ? (sale?.receipt_number ?? null) : null}
                onPrint={!isDraft && saleId ? () => setPrintOpen(true) : undefined}
                onNewSale={onNewSale}
                newSaleHint={keyboard ? "Новая продажа (F2)" : undefined}
                touch={touch}
                completeHint={keyboard ? "Завершить продажу (F4)" : undefined}
              />
            </div>
          </div>
        </div>
      </div>

      {isDraft && totalDue > 0 && !paymentPanelVisible && (
        <div
          className="fixed inset-x-3 bottom-3 z-sticky mx-auto flex max-w-md items-center justify-between gap-3 rounded-lg border border-border bg-surface-raised p-2 shadow-lg xl:hidden"
          role="region"
          aria-label="Краткая сумма чека"
        >
          <div className="min-w-0 px-2">
            <p className="text-xs text-foreground-muted">К оплате</p>
            <p
              className="truncate font-mono text-lg font-semibold tabular-nums text-foreground"
              aria-live="polite"
            >
              {Math.max(0, remaining).toFixed(2)} {currency}
            </p>
          </div>
          <Button
            size="lg"
            onClick={() => {
              paymentPanelRef.current?.scrollIntoView({ block: "start" });
              paymentPanelRef.current
                ?.querySelector<HTMLElement>(".pos-tile:not(:disabled)")
                ?.focus();
            }}
          >
            Перейти к оплате
          </Button>
        </div>
      )}

      {saleId && (
        <PrescriptionModal
          open={prescriptionOpen}
          onClose={() => setPrescriptionOpen(false)}
          onSaved={(payload) => {
            setPrescription(payload);
            setPrescriptionOpen(false);
          }}
        />
      )}

      {numpad && (
        <Suspense fallback={null}>
          <NumPad
            title={numpad.kind === "qty" ? "Количество" : "Сумма оплаты"}
            initial={numpad.initial}
            allowDecimal={numpad.kind === "payment"}
            onSubmit={onNumpadSubmit}
            onClose={() => setNumpad(null)}
          />
        </Suspense>
      )}

      {printOpen && saleId && (
        <ReceiptPrintModal
          saleId={saleId}
          registerId={registerId}
          onClose={() => setPrintOpen(false)}
        />
      )}

      <ConfirmDialog
        open={discardConfirmOpen}
        title="Начать новую продажу"
        message="Текущий незавершённый чек будет очищен. Это действие нельзя отменить."
        confirmLabel="Очистить чек"
        variant="danger"
        onConfirm={() => {
          if (!startNewSale()) return;
          setDiscardConfirmOpen(false);
          requestAnimationFrame(() => searchRef.current?.focus());
        }}
        onCancel={() => setDiscardConfirmOpen(false)}
      />
    </>
  );
}

function productCountLabel(count: number): string {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return "товаров";
  if (mod10 === 1) return "товар";
  if (mod10 >= 2 && mod10 <= 4) return "товара";
  return "товаров";
}

function ClearReceiptIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
    </svg>
  );
}
