import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Button, ConfirmDialog } from "@/components/ui";
import { findByBarcode } from "@/features/catalog/api";
import { type CatalogItem } from "@/features/catalog/types";
import { requestDesktopCashDrawerOpen } from "@/lib/desktopBridge";
import { describeApiError } from "@/lib/errorMessages";
import { useOnlineStatus } from "@/lib/useOnlineStatus";
import { cn } from "@/lib/utils";

import { BarcodeListener } from "./BarcodeListener";
import { CartList } from "./CartList";
import { PaymentPanel } from "./PaymentPanel";
import { PrescriptionModal } from "./PrescriptionModal";
import { QuickProducts } from "./QuickProducts";
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
  useAddPosFavorite,
  useAddPrescription,
  useBeginPaymentAttemptReconciliation,
  useCheckoutSale,
  useCompleteSale,
  useConfirmPaymentAttempt,
  useCreatePaymentAttempt,
  useCurrentShiftQuery,
  usePosFavoritesQuery,
  useRemovePosFavorite,
  useSaleQuery,
  useVoidPaymentAttempt,
} from "./queries";
import {
  clearPaymentAttemptOperation,
  createPaymentAttemptOperation,
  loadPaymentAttemptOperation,
} from "./paymentAttemptOperation";
import {
  clearPendingPaymentOperation,
  createPendingPaymentOperation,
  loadPendingPaymentOperation,
  type PendingPaymentOperation,
} from "./paymentOperation";
import { type AppliedPosCommand, usePosCommandResilience } from "./usePosCommandResilience";
import {
  type Payment,
  type PaymentAttempt,
  type PaymentAttemptConfirmPayload,
  type PaymentMetadata,
  type PaymentMethod,
  type PaymentMethodRead,
  type PrescriptionLogPayload,
  type SaleCheckoutResult,
  type SaleDetails,
} from "./types";
import { type PosMode } from "./usePosMode";

const ReceiptPrintModal = lazy(async () => {
  const module = await import("./ReceiptPrintModal");
  return { default: module.ReceiptPrintModal };
});

// Lazy so the on-screen keypad chunk only loads when a cashier taps a field.
const NumPad = lazy(() => import("./NumPad"));
const ExternalPaymentEvidenceDialog = lazy(async () => {
  const module = await import("./ExternalPaymentEvidenceDialog");
  return { default: module.ExternalPaymentEvidenceDialog };
});

type NumPadState =
  | { kind: "qty"; itemId: string; initial: string }
  | { kind: "payment"; method: PaymentMethod; initial: string };

type FlashTone = "success" | "danger";
type CheckoutRecoveryOutcome = "resolved" | "not-found" | "unavailable" | "conflict";
type StagedPayment = Omit<Payment, "payment_method" | "payment_attempt_id"> & {
  payment_method: PaymentMethod;
  payment_attempt_id?: string;
  metadata?: PaymentMetadata;
};
type ExternalPaymentConfirmation = {
  method: "card" | "qr";
  amount: string;
  attempt: PaymentAttempt;
};
function isDefiniteRejection(error: unknown): boolean {
  if (!isAxiosError(error) || error.response === undefined) return false;
  const status = error.response.status;
  return status >= 400 && status < 500 && status !== 408 && status !== 409;
}

function isExpiredSaleBlocked(error: unknown): boolean {
  if (!isAxiosError(error)) return false;
  const data = error.response?.data as { error?: { details?: { reason?: unknown } } } | undefined;
  return data?.error?.details?.reason === "expired_batch_blocked";
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

function normalizePositiveMoney(value: string): string | null {
  const normalized = value.trim().replace(",", ".");
  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(normalized)) return null;
  const amount = Number(normalized);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  return amount.toFixed(2);
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
  canReconcileExternalPayment = false,
  workstationControls,
  onRegisterSwitchStateChange,
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
  canReconcileExternalPayment?: boolean;
  workstationControls?: ReactNode;
  onRegisterSwitchStateChange?: (state: RegisterSwitchState) => void;
}): JSX.Element {
  const isOnline = useOnlineStatus();
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
          canReconcileExternalPayment={canReconcileExternalPayment}
          online={isOnline}
          workstationControls={workstationControls}
          onRegisterSwitchStateChange={onRegisterSwitchStateChange}
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
            online={isOnline}
          />
        </div>
      )}
    </div>
  );
}

export interface RegisterSwitchState {
  blocked: boolean;
  hasDraft: boolean;
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
  canReconcileExternalPayment,
  online,
  workstationControls,
  onRegisterSwitchStateChange,
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
  canReconcileExternalPayment: boolean;
  online: boolean;
  workstationControls?: ReactNode;
  onRegisterSwitchStateChange?: (state: RegisterSwitchState) => void;
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
  const [stagedPayments, setStagedPayments] = useState<StagedPayment[]>(() =>
    init.stagedPayments.map((payment, index) => ({
      id: `staged-${index + 1}`,
      sale_id: init.saleId ?? "",
      operation_id: null,
      payment_method: payment.payment_method,
      amount: payment.amount,
      currency: "TJS",
      payment_attempt_id: payment.payment_attempt_id,
      metadata: payment.metadata,
    })),
  );
  const [payingMethod, setPayingMethod] = useState<PaymentMethodRead | null>(null);
  const [numpad, setNumpad] = useState<NumPadState | null>(null);
  const [flash, setFlash] = useState<FlashTone | null>(null);
  const [printOpen, setPrintOpen] = useState(false);
  const [discardConfirmOpen, setDiscardConfirmOpen] = useState(false);
  const [externalPaymentReviewRequired, setExternalPaymentReviewRequired] = useState(
    init.externalPaymentReviewRequired,
  );
  const [externalPaymentConfirmation, setExternalPaymentConfirmation] =
    useState<ExternalPaymentConfirmation | null>(null);
  const [paymentPanelVisible, setPaymentPanelVisible] = useState(false);
  const [queuedScans, setQueuedScans] = useState(0);
  const [favoriteError, setFavoriteError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const paymentPanelRef = useRef<HTMLDivElement>(null);
  const flashTimer = useRef<number | undefined>(undefined);
  const completingRef = useRef(false);
  const checkoutRecoveryRef = useRef(false);
  const openedDrawerOperationRef = useRef<string | null>(null);
  const stagedPaymentSequenceRef = useRef(init.stagedPayments.length);
  const stagedPaymentsRef = useRef(stagedPayments);
  const externalPaymentConfirmationRef = useRef<ExternalPaymentConfirmation | null>(null);
  const externalPaymentMutationRef = useRef(false);
  const externalPaymentReviewRef = useRef(externalPaymentReviewRequired);
  const saleIdRef = useRef<string | null>(saleId);
  const scanQueueRef = useRef<Promise<void>>(Promise.resolve());
  saleIdRef.current = saleId;
  stagedPaymentsRef.current = stagedPayments;
  externalPaymentReviewRef.current = externalPaymentReviewRequired;

  const onPosCommandApplied = useCallback((applied: AppliedPosCommand) => {
    saleIdRef.current = applied.sale.id;
    setSaleId(applied.sale.id);
    setStaleNotice(false);
    if (
      applied.command.commandType === "item.add" &&
      typeof applied.result === "object" &&
      applied.result !== null &&
      "requires_prescription_log" in applied.result &&
      applied.result.requires_prescription_log === true
    ) {
      setRequiresRx(true);
      setPrescriptionOpen(true);
    }
  }, []);
  const posCommand = usePosCommandResilience({
    registerId,
    onApplied: onPosCommandApplied,
  });
  const addPayment = useAddPayment();
  const addPrescription = useAddPrescription();
  const completeSale = useCompleteSale();
  const checkoutSale = useCheckoutSale();
  const createPaymentAttempt = useCreatePaymentAttempt();
  const beginPaymentAttemptReconciliation = useBeginPaymentAttemptReconciliation();
  const confirmPaymentAttempt = useConfirmPaymentAttempt();
  const voidPaymentAttempt = useVoidPaymentAttempt();
  const saleQuery = useSaleQuery(saleId);
  const favorites = usePosFavoritesQuery(branchId ?? undefined);
  const addFavorite = useAddPosFavorite();
  const removeFavorite = useRemovePosFavorite();
  const refetchSale = saleQuery.refetch;
  const favoriteCatalogIds = useMemo(
    () => new Set(favorites.data?.map((favorite) => favorite.catalog_id) ?? []),
    [favorites.data],
  );
  const favoritePendingId = addFavorite.isPending
    ? addFavorite.variables
    : removeFavorite.isPending
      ? removeFavorite.variables
      : null;

  const toggleFavorite = async (item: CatalogItem) => {
    if (favoritePendingId !== null) return;
    setFavoriteError(null);
    try {
      if (favoriteCatalogIds.has(item.id)) {
        await removeFavorite.mutateAsync(item.id);
      } else {
        await addFavorite.mutateAsync(item.id);
      }
    } catch (error) {
      setFavoriteError(describeApiError(error, "Не удалось изменить избранное"));
    }
  };

  const sale: SaleDetails | null = saleQuery.data ?? null;
  const isDraft = !sale || sale.status === "draft";
  const items = sale?.items ?? [];
  const recordedPayments = sale?.payments ?? [];
  const payments =
    sale?.status === "draft" ? [...recordedPayments, ...stagedPayments] : recordedPayments;
  const currency = sale?.currency ?? "TJS";
  const totalDue = sale ? Number(sale.total_amount) : 0;
  const totalPaid = payments.reduce((sum, p) => sum + Number(p.amount), 0);
  const remaining = totalDue - totalPaid;
  const stagedTotal = stagedPayments.reduce((sum, payment) => sum + Number(payment.amount), 0);
  const electronicPaymentPendingResolution = stagedPayments.some(
    (payment) => payment.payment_method === "card" || payment.payment_method === "qr",
  );
  const stagedPaymentConflict =
    stagedPayments.length > 0 &&
    sale?.status === "draft" &&
    (recordedPayments.length > 0 ||
      stagedTotal - totalDue > 0.001 ||
      (!electronicPaymentPendingResolution &&
        !paymentSettingsLoading &&
        !paymentSettingsUnavailable &&
        (stagedPayments.some((payment) => !paymentMethods.includes(payment.payment_method)) ||
          (!mixedPaymentEnabled &&
            (stagedPayments.length > 1 || stagedTotal + 0.001 < totalDue)))));
  const cartMutationPending = posCommand.blocked || !online;
  const paymentStarted = stagedPayments.length > 0 || recordedPayments.length > 0;
  const saleEditingBlocked =
    !online ||
    completeSale.isPending ||
    checkoutSale.isPending ||
    posCommand.blocked ||
    paymentStarted ||
    completionUncertain ||
    checkoutUncertain ||
    pendingCheckout !== null ||
    pendingPayment !== null ||
    (saleId !== null && sale === null);
  const scannerHardwareBlocked =
    !online ||
    completeSale.isPending ||
    checkoutSale.isPending ||
    paymentStarted ||
    completionUncertain ||
    checkoutUncertain ||
    pendingCheckout !== null ||
    pendingPayment !== null;
  const scanInputBlocked = scannerHardwareBlocked || posCommand.blocked;
  const shiftCloseBlocked =
    !online ||
    completeSale.isPending ||
    checkoutSale.isPending ||
    checkoutReconciling ||
    posCommand.blocked ||
    addPayment.isPending ||
    createPaymentAttempt.isPending ||
    confirmPaymentAttempt.isPending ||
    voidPaymentAttempt.isPending ||
    addPrescription.isPending ||
    completionUncertain ||
    checkoutUncertain ||
    pendingCheckout !== null ||
    pendingPayment !== null ||
    (saleId !== null && sale === null) ||
    (sale?.status === "draft" &&
      (items.length > 0 ||
        recordedPayments.length > 0 ||
        stagedPayments.length > 0 ||
        prescription !== null));
  const registerSwitchBlocked =
    !online ||
    completeSale.isPending ||
    checkoutSale.isPending ||
    checkoutReconciling ||
    posCommand.blocked ||
    addPayment.isPending ||
    createPaymentAttempt.isPending ||
    confirmPaymentAttempt.isPending ||
    voidPaymentAttempt.isPending ||
    addPrescription.isPending ||
    completionUncertain ||
    checkoutUncertain ||
    pendingCheckout !== null ||
    pendingPayment !== null ||
    queuedScans > 0 ||
    electronicPaymentPendingResolution ||
    externalPaymentReviewRequired ||
    (saleId !== null && sale === null);
  const hasDraftToPreserve =
    sale?.status === "draft" &&
    (items.length > 0 ||
      recordedPayments.length > 0 ||
      stagedPayments.length > 0 ||
      prescription !== null);
  useEffect(() => {
    onRegisterSwitchStateChange?.({
      blocked: registerSwitchBlocked,
      hasDraft: hasDraftToPreserve,
    });
  }, [hasDraftToPreserve, onRegisterSwitchStateChange, registerSwitchBlocked]);

  useEffect(
    () => () => onRegisterSwitchStateChange?.({ blocked: false, hasDraft: false }),
    [onRegisterSwitchStateChange],
  );
  useEffect(() => {
    if (!stagedPaymentConflict) return;
    if (electronicPaymentPendingResolution) {
      setExternalPaymentReviewRequired(true);
    }
    setTopError(
      electronicPaymentPendingResolution
        ? "Подтверждённую электронную оплату нельзя сбросить на кассе. Завершите чек или передайте сверку ответственному сотруднику."
        : "Расчёт оплаты больше не соответствует чеку или настройкам. Сбросьте расчёт перед новой оплатой.",
    );
  }, [electronicPaymentPendingResolution, stagedPaymentConflict]);

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
      stagedPaymentsRef.current = [];
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
          setPendingCheckout(operation);
          setCheckoutUncertain(true);
          setTopError(
            "Операция пока не найдена на сервере Aurum. Проверьте внешний терминал и повторите завершение: запрос будет отправлен с тем же безопасным номером операции.",
          );
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
      saveDraft(
        registerId,
        sale.id,
        nameById,
        "draft",
        requiresRx,
        stagedPayments,
        false,
        externalPaymentReviewRequired,
      );
    }
  }, [sale, nameById, registerId, requiresRx, stagedPayments, externalPaymentReviewRequired]);

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
  const ensureSaleId = useCallback(async (): Promise<string | null> => {
    if (saleIdRef.current) return saleIdRef.current;
    const outcome = await posCommand.begin({
      commandType: "sale.create",
      registerId,
    });
    if (outcome.rejectedError !== undefined) throw outcome.rejectedError;
    return outcome.applied?.sale.id ?? null;
  }, [posCommand, registerId]);

  const onAdd = async (
    catalogId: string,
    name: string,
    qty: number,
    fromScanner = false,
  ): Promise<boolean> => {
    if (fromScanner ? scannerHardwareBlocked : saleEditingBlocked) return false;
    setTopError(null);
    try {
      const id = await ensureSaleId();
      if (!id) return false;
      if (name) setNameById((m) => ({ ...m, [catalogId]: name }));
      const outcome = await posCommand.begin({
        commandType: "item.add",
        registerId,
        saleId: id,
        catalogId,
        qty: String(qty),
        expiredSaleConfirmed: false,
      });
      if (outcome.rejectedError !== undefined) throw outcome.rejectedError;
      if (!outcome.applied) return false;
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
      const added = await onAdd(item.id, item.brand_name, 1, true);
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
    setQueuedScans((count) => count + 1);
    scanQueueRef.current = scanQueueRef.current
      .then(
        () => onScan(code),
        () => onScan(code),
      )
      .finally(() => setQueuedScans((count) => Math.max(0, count - 1)));
  };

  const onQtyChange = async (itemId: string, qty: number) => {
    if (!saleId || saleEditingBlocked) return;
    setTopError(null);
    try {
      const outcome = await posCommand.begin({
        commandType: "item.update",
        registerId,
        saleId,
        itemId,
        qty: String(qty),
      });
      if (outcome.rejectedError !== undefined) throw outcome.rejectedError;
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось изменить количество"));
    }
  };

  const onDelete = async (itemId: string) => {
    if (!saleId || saleEditingBlocked) return;
    try {
      const outcome = await posCommand.begin({
        commandType: "item.delete",
        registerId,
        saleId,
        itemId,
      });
      if (outcome.rejectedError !== undefined) throw outcome.rejectedError;
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось удалить"));
    }
  };

  const payLegacy = async (
    method: PaymentMethodRead,
    amount: string,
    metadata?: PaymentMetadata,
  ) => {
    if (!saleId) return;
    const amt = Number(amount);
    if (!(amt > 0)) return;
    const normalizedAmount = amt.toFixed(2);
    const storedOperation =
      pendingPayment?.saleId === saleId ? pendingPayment : loadPendingPaymentOperation(saleId);
    if (!storedOperation && (method === "bank_transfer" || !paymentMethods.includes(method))) {
      setTopError("Этот способ оплаты больше не доступен для новых операций.");
      return;
    }
    const recordedMethod = recordedPayments[0]?.payment_method;
    if (!mixedPaymentEnabled && recordedMethod !== undefined && recordedMethod !== method) {
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

    if (!saveDraft(registerId, saleId, nameById, "draft", requiresRx, [], false, false)) {
      setTopError(
        "Локальное хранилище кассы недоступно. Оплата не отправлена; освободите место или перезапустите приложение.",
      );
      return;
    }
    const operation =
      storedOperation ??
      (method === "bank_transfer"
        ? null
        : createPendingPaymentOperation(saleId, method, normalizedAmount, metadata));
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
          metadata: storedOperation?.metadata ?? metadata,
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

  const stagePayment = (
    method: PaymentMethod,
    amount: string,
    metadata?: PaymentMetadata,
    paymentAttemptId?: string,
    serverConfirmed = false,
  ): boolean => {
    if (!saleId || recordedPayments.length > 0) return false;
    if (!serverConfirmed && !paymentMethods.includes(method)) {
      setTopError("Этот способ оплаты отключён в настройках аптеки.");
      return false;
    }
    const normalizedAmount = normalizePositiveMoney(amount);
    if (!normalizedAmount) {
      setTopError("Введите корректную сумму оплаты не более чем с двумя знаками после запятой.");
      return false;
    }
    const numericAmount = Number(normalizedAmount);
    const currentPayments = stagedPaymentsRef.current;
    const currentStagedTotal = currentPayments.reduce(
      (sum, payment) => sum + Number(payment.amount),
      0,
    );
    const recordedTotal = recordedPayments.reduce(
      (sum, payment) => sum + Number(payment.amount),
      0,
    );
    const currentRemaining = totalDue - recordedTotal - currentStagedTotal;
    if (currentPayments.length >= 10) {
      setTopError("В одном чеке допускается не более 10 частей смешанной оплаты.");
      return false;
    }
    if (
      !serverConfirmed &&
      !mixedPaymentEnabled &&
      (currentPayments.length > 0 || numericAmount + 0.001 < currentRemaining)
    ) {
      setTopError("Смешанная оплата отключена. Внесите всю сумму одним способом.");
      return false;
    }
    if (numericAmount - currentRemaining > 0.001) {
      setTopError(
        `Сумма оплаты превышает остаток ${Math.max(0, currentRemaining).toFixed(2)} ${currency}.`,
      );
      return false;
    }

    let safeMetadata: PaymentMetadata | undefined;
    if (metadata?.cash_received !== undefined) {
      const cashReceived = normalizePositiveMoney(metadata.cash_received);
      if (method !== "cash" || !cashReceived || Number(cashReceived) + 0.001 < numericAmount) {
        setTopError("Полученная сумма наличными не может быть меньше суммы оплаты.");
        return false;
      }
      safeMetadata = { cash_received: cashReceived };
    }
    if (method === "card" || method === "qr") {
      if (!paymentAttemptId) {
        setTopError("Электронная оплата не привязана к подтверждённой операции.");
        return false;
      }
      safeMetadata = undefined;
    } else if (paymentAttemptId) {
      setTopError("Платёжная попытка недопустима для наличной оплаты.");
      return false;
    }

    stagedPaymentSequenceRef.current += 1;
    const nextPayments: StagedPayment[] = [
      ...currentPayments,
      {
        id: `staged-${stagedPaymentSequenceRef.current}`,
        sale_id: saleId,
        operation_id: null,
        payment_method: method,
        amount: normalizedAmount,
        currency,
        payment_attempt_id: paymentAttemptId,
        metadata: safeMetadata,
      },
    ];
    if (
      !saveDraft(
        registerId,
        saleId,
        nameById,
        "draft",
        requiresRx,
        nextPayments,
        false,
        externalPaymentReviewRef.current,
      )
    ) {
      setTopError(
        "Локальное хранилище кассы недоступно. Расчёт оплаты не сохранён; освободите место или перезапустите приложение.",
      );
      return false;
    }
    stagedPaymentsRef.current = nextPayments;
    setStagedPayments(nextPayments);
    setTopError(null);
    return true;
  };

  const applyPayment = (method: PaymentMethod, amount: string, metadata?: PaymentMetadata) => {
    if (recordedPayments.length > 0 || pendingPayment !== null) {
      void payLegacy(method, amount, metadata);
      return;
    }
    stagePayment(method, amount, metadata);
  };

  const submitPayment = (method: PaymentMethod, amount: string, metadata?: PaymentMetadata) => {
    if (method === "card" || method === "qr") {
      if (recordedPayments.length > 0 || pendingPayment !== null) {
        setTopError(
          "Электронную оплату нельзя добавлять через устаревший режим чека. Завершите или отмените предыдущую операцию.",
        );
        return;
      }
      void prepareExternalPayment(method, amount);
      return;
    }
    applyPayment(method, amount, metadata);
  };

  const prepareExternalPayment = async (method: "card" | "qr", amount: string) => {
    if (!saleId || externalPaymentConfirmationRef.current !== null) return;
    const normalizedAmount = normalizePositiveMoney(amount);
    if (!normalizedAmount) {
      setTopError("Введите корректную сумму электронной оплаты.");
      return;
    }
    const operation = createPaymentAttemptOperation(saleId, method, normalizedAmount);
    if (!operation) {
      setTopError(
        "Не удалось сохранить безопасный номер электронной оплаты. Терминал не используйте; освободите место или перезапустите приложение.",
      );
      return;
    }
    setPayingMethod(method);
    setTopError(null);
    try {
      let attempt = await createPaymentAttempt.mutateAsync({
        operation_id: operation.operationId,
        sale_id: saleId,
        payment_method: method,
        amount: normalizedAmount,
      });
      if (attempt.status === "confirmed") {
        if (stagePayment(method, normalizedAmount, undefined, attempt.id, true)) {
          clearPaymentAttemptOperation(saleId, operation.operationId);
        }
        return;
      }
      if (attempt.status === "pending") {
        attempt = await beginPaymentAttemptReconciliation.mutateAsync(attempt.id);
      }
      if (attempt.status !== "requires_reconciliation") {
        clearPaymentAttemptOperation(saleId, operation.operationId);
        setTopError("Эта электронная операция уже закрыта. Создайте новую оплату.");
        return;
      }
      const confirmation: ExternalPaymentConfirmation = {
        method,
        amount: normalizedAmount,
        attempt,
      };
      externalPaymentConfirmationRef.current = confirmation;
      setExternalPaymentConfirmation(confirmation);
    } catch (error) {
      setTopError(
        describeApiError(
          error,
          "Не удалось подготовить электронную оплату. Терминал не используйте; после восстановления связи повторите тот же способ оплаты.",
        ),
      );
    } finally {
      setPayingMethod(null);
    }
  };

  const closeExternalPaymentConfirmation = () => {
    externalPaymentConfirmationRef.current = null;
    setExternalPaymentConfirmation(null);
  };

  const cancelExternalPayment = async (evidence: PaymentAttemptConfirmPayload) => {
    if (
      externalPaymentMutationRef.current ||
      voidPaymentAttempt.isPending ||
      confirmPaymentAttempt.isPending
    )
      return;
    const confirmation = externalPaymentConfirmationRef.current;
    if (!confirmation) return;
    externalPaymentMutationRef.current = true;
    try {
      await voidPaymentAttempt.mutateAsync({
        attemptId: confirmation.attempt.id,
        payload: {
          reason: "terminal_declined",
          terminal_id: evidence.terminal_id,
          external_reference: evidence.external_reference,
        },
      });
      clearPaymentAttemptOperation(confirmation.attempt.sale_id, confirmation.attempt.operation_id);
      closeExternalPaymentConfirmation();
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось отменить подготовленную оплату"));
    } finally {
      externalPaymentMutationRef.current = false;
    }
  };

  const confirmExternalPayment = async (evidence: PaymentAttemptConfirmPayload) => {
    if (
      externalPaymentMutationRef.current ||
      confirmPaymentAttempt.isPending ||
      voidPaymentAttempt.isPending
    )
      return;
    const confirmation = externalPaymentConfirmationRef.current;
    if (!confirmation) return;
    externalPaymentMutationRef.current = true;
    try {
      const attempt = await confirmPaymentAttempt.mutateAsync({
        attemptId: confirmation.attempt.id,
        payload: evidence,
      });
      if (attempt.status !== "confirmed") {
        setTopError("Сервер не подтвердил электронную оплату. Не завершайте чек.");
        return;
      }
      if (!stagePayment(confirmation.method, confirmation.amount, undefined, attempt.id, true))
        return;
      clearPaymentAttemptOperation(attempt.sale_id, attempt.operation_id);
      closeExternalPaymentConfirmation();
    } catch (error) {
      setTopError(
        describeApiError(
          error,
          "Не удалось сохранить подтверждение. Не повторяйте оплату на терминале; повторите подтверждение в Aurum.",
        ),
      );
    } finally {
      externalPaymentMutationRef.current = false;
    }
  };

  const clearStagedPaymentCalculation = () => {
    if (!saleId || stagedPaymentsRef.current.length === 0) return;
    if (checkoutSale.isPending || checkoutReconciling || pendingCheckout || checkoutUncertain) {
      setTopError("Дождитесь проверки текущей денежной операции.");
      return;
    }
    if (
      stagedPaymentsRef.current.some(
        (payment) => payment.payment_method === "card" || payment.payment_method === "qr",
      )
    ) {
      setTopError(
        "Подтверждённую электронную оплату нельзя сбросить на кассе. Завершите чек или передайте сверку ответственному сотруднику.",
      );
      return;
    }
    if (!saveDraft(registerId, saleId, nameById, "draft", requiresRx, [], false, false)) {
      setTopError(
        "Не удалось сохранить сброс расчёта оплаты. Освободите место или перезапустите приложение.",
      );
      return;
    }
    stagedPaymentsRef.current = [];
    setStagedPayments([]);
    externalPaymentReviewRef.current = false;
    setExternalPaymentReviewRequired(false);
    setTopError(null);
  };

  const requestStagedPaymentReset = () => {
    clearStagedPaymentCalculation();
  };

  // Touch: tapping a tile opens the keypad pre-filled with the remaining amount
  // (so partial payments are easy). Keyboard/desktop: one tap pays the rest.
  const onPayTile = (
    method: PaymentMethod,
    requestedAmount?: string,
    metadata?: PaymentMetadata,
  ) => {
    if (
      !saleId ||
      remaining <= 0.001 ||
      cartMutationPending ||
      paymentSettingsLoading ||
      paymentSettingsUnavailable ||
      !paymentMethods.includes(method) ||
      stagedPaymentConflict
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
      const requested = Number(requestedAmount);
      const amount = method === "cash" ? Math.min(requested, remaining) : requested;
      if (Number.isFinite(amount) && amount > 0) {
        submitPayment(method, amount.toFixed(2), metadata);
      }
      return;
    }
    if (touch || (mixedPaymentEnabled && paymentMethods.length > 1)) {
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
    if (remaining < -0.001) {
      setTopError(`Оплата превышает сумму чека на ${Math.abs(remaining).toFixed(2)} ${currency}.`);
      return;
    }
    if (!saveDraft(registerId, saleId, nameById, "draft", requiresRx, [], false, false)) {
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
      await completeSale.mutateAsync({
        saleId,
        expiredSaleConfirmed: false,
      });
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
        setTopError(
          isExpiredSaleBlocked(err)
            ? "Просроченные лекарства нельзя продавать. Удалите позицию из чека."
            : describeApiError(err, "Не удалось завершить продажу"),
        );
      }
      completingRef.current = false;
    }
  };

  const onComplete = async () => {
    if (!saleId || !sale || completingRef.current) return;
    if (cartMutationPending) {
      setTopError("Дождитесь завершения изменения товаров в чеке.");
      return;
    }
    if (recordedPayments.length > 0 || pendingPayment || completionUncertain) {
      await completeLegacySale();
      return;
    }
    if (checkoutSale.isPending || checkoutReconciling) return;
    if (externalPaymentReviewRequired) {
      setTopError(
        "Продажа отклонена после подтверждения карты или QR. Не повторяйте оплату: сначала сверьте терминал и отмените подтверждённую операцию.",
      );
      return;
    }
    if (paymentSettingsLoading || paymentSettingsUnavailable) {
      setTopError("Дождитесь подтверждённых настроек способов оплаты.");
      return;
    }
    if (stagedPaymentConflict) {
      setTopError(
        "Расчёт оплаты конфликтует с чеком или настройками. Проверьте внешний терминал и сбросьте расчёт.",
      );
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
    if (remaining < -0.001) {
      setTopError(`Оплата превышает сумму чека на ${Math.abs(remaining).toFixed(2)} ${currency}.`);
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
      const recovery = await recoverCheckout(storedOperation, true);
      if (recovery !== "not-found") return;
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
    if (
      !saveDraft(
        registerId,
        saleId,
        nameById,
        "draft",
        requiresRx,
        stagedPayments,
        false,
        externalPaymentReviewRef.current,
      )
    ) {
      setTopError(
        "Локальное хранилище кассы недоступно. Продажа не отправлена; освободите место или перезапустите приложение.",
      );
      return;
    }

    const operation = storedOperation ?? createPendingCheckoutOperation(saleId, registerId);
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
          payment_attempt_id: payment.payment_attempt_id,
          metadata: payment.metadata,
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
        if (electronicPaymentPendingResolution) {
          setExternalPaymentReviewRequired(true);
          setTopError(
            "Сервер отклонил продажу после подтверждения карты или QR. Не повторяйте оплату и не начинайте новый чек. Передайте операцию ответственному сотруднику для сверки.",
          );
        } else {
          setTopError(
            isExpiredSaleBlocked(error)
              ? "Просроченные лекарства нельзя продавать. Удалите позицию из чека."
              : describeApiError(error, "Не удалось оформить продажу"),
          );
        }
      } else {
        setCheckoutUncertain(true);
        const outcome = await recoverCheckout(operation, true);
        if (outcome === "not-found") {
          setTopError(
            "Ответ сервера не получен, а операция пока не найдена. Проверьте внешний терминал и повторите завершение: Aurum использует тот же безопасный номер операции.",
          );
        }
      }
    } finally {
      completingRef.current = false;
    }
  };

  const startNewSale = (): boolean => {
    if (posCommand.blocked) {
      setTopError("Сначала завершите сверку последней команды с сервером.");
      return false;
    }
    if (
      sale?.status === "draft" &&
      saleId &&
      (stagedPaymentsRef.current.some(
        (payment) => payment.payment_method === "card" || payment.payment_method === "qr",
      ) ||
        externalPaymentReviewRef.current ||
        loadPaymentAttemptOperation(saleId) !== null)
    ) {
      setTopError(
        "Сначала завершите чек или передайте электронную оплату ответственному сотруднику для сверки. Новый чек не начат.",
      );
      return false;
    }
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
      clearPaymentAttemptOperation(saleId);
    }
    saleIdRef.current = null;
    setSaleId(null);
    setNameById({});
    setRequiresRx(false);
    setPrescription(null);
    stagedPaymentsRef.current = [];
    setStagedPayments([]);
    closeExternalPaymentConfirmation();
    setPendingCheckout(null);
    setCheckoutUncertain(false);
    externalPaymentReviewRef.current = false;
    setExternalPaymentReviewRequired(false);
    setTopError(null);
    setStaleNotice(false);
    return true;
  };

  const onNewSale = (): boolean => {
    if (
      completingRef.current ||
      posCommand.blocked ||
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
    if (isDraft && recordedPayments.length > 0) {
      setTopError("В чеке уже есть проведённая оплата. Завершите текущую продажу.");
      return false;
    }
    if (isDraft && stagedPayments.length > 0) {
      setTopError(
        electronicPaymentPendingResolution
          ? "Подтверждённую электронную оплату нельзя удалить. Завершите чек или передайте сверку ответственному сотруднику."
          : "Сначала нажмите «Сбросить расчёт», затем начните новую продажу.",
      );
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
      submitPayment(
        numpad.method,
        value,
        numpad.method === "cash" ? { cash_received: value } : undefined,
      );
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
      remaining > 0.001 &&
      !cartMutationPending
    ) {
      const amount = remaining.toFixed(2);
      onPayTile(
        defaultMethod,
        amount,
        defaultMethod === "cash" ? { cash_received: amount } : undefined,
      );
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
      <BarcodeListener enabled={isDraft && !scannerHardwareBlocked} onScan={enqueueScan} />

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

      <div className="space-y-3 xl:flex xl:h-full xl:min-h-0 xl:flex-col xl:gap-2 xl:space-y-0">
        <div className="grid min-w-0 shrink-0 gap-2 xl:grid-cols-[auto_minmax(0,1fr)]">
          {workstationControls ? (
            <div className="flex min-w-0 items-center rounded-lg border border-border bg-surface px-3 py-2">
              {workstationControls}
            </div>
          ) : null}
          <ShiftBar
            registerId={registerId}
            mode={mode}
            canClose={canCloseShift}
            closeBlocked={shiftCloseBlocked}
            online={online}
          />
        </div>

        {staleNotice && (
          <p className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
            Прошлый черновик устарел (более {draftTtlMin} мин) и был очищен — начните новую продажу.
          </p>
        )}

        {!online ? (
          <p
            className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground"
            role="status"
          >
            Нет связи с сервером. Текущий чек сохранён на этом устройстве; изменение товаров, оплата
            и закрытие смены временно заблокированы.
          </p>
        ) : null}

        {posCommand.message ? (
          <div
            role="status"
            className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground"
          >
            <p>{posCommand.message}</p>
            {posCommand.canRetry ? (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                isLoading={posCommand.isWorking}
                onClick={() => void posCommand.retry()}
              >
                Повторить
              </Button>
            ) : null}
          </div>
        ) : null}

        {isDraft && (
          <fieldset disabled={saleEditingBlocked} className="min-w-0 shrink-0 border-0 p-0">
            <SearchBar
              ref={searchRef}
              onAdd={onAdd}
              busy={posCommand.blocked}
              scanner={scanInputBlocked ? "off" : queuedScans > 0 ? "scanning" : "ready"}
              queuedScans={queuedScans}
              touch={touch}
              branchId={branchId ?? undefined}
              favoriteCatalogIds={favoriteCatalogIds}
              favoritePendingId={favoritePendingId}
              favoriteError={favoriteError}
              onFavoriteToggle={(item) => void toggleFavorite(item)}
            />
          </fieldset>
        )}

        <div className="grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-12 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(18rem,1.15fr)_minmax(22rem,1fr)_minmax(18rem,0.78fr)] xl:gap-2">
          <div className="min-w-0 lg:col-span-5 xl:col-auto xl:min-h-0">
            <QuickProducts
              branchId={branchId ?? undefined}
              onAdd={onAdd}
              busy={!isDraft || saleEditingBlocked || posCommand.blocked}
              touch={touch}
            />
          </div>

          <section
            aria-labelledby="current-receipt-title"
            className={cn(
              "flex min-h-[26rem] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-surface sm:min-h-[30rem] lg:col-span-7 xl:col-auto xl:h-full xl:min-h-0",
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
              busy={posCommand.blocked}
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
            className="min-w-0 scroll-mt-20 pb-20 lg:col-span-12 lg:pb-0 xl:col-auto xl:min-h-0"
          >
            <div className="xl:h-full xl:min-h-0">
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
                interactionBlocked={
                  cartMutationPending ||
                  createPaymentAttempt.isPending ||
                  confirmPaymentAttempt.isPending ||
                  voidPaymentAttempt.isPending ||
                  externalPaymentReviewRequired
                }
                completionBlocked={
                  cartMutationPending ||
                  stagedPaymentConflict ||
                  externalPaymentReviewRequired ||
                  (recordedPayments.length === 0 &&
                    (paymentSettingsLoading ||
                      paymentSettingsUnavailable ||
                      stagedPayments.length === 0))
                }
                onPayTile={onPayTile}
                onRetryPendingPayment={
                  pendingPayment
                    ? () =>
                        void payLegacy(
                          pendingPayment.paymentMethod,
                          pendingPayment.amount,
                          pendingPayment.metadata,
                        )
                    : undefined
                }
                onClearPayments={
                  stagedPayments.length > 0 &&
                  recordedPayments.length === 0 &&
                  !electronicPaymentPendingResolution
                    ? requestStagedPaymentReset
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
        <Suspense fallback={null}>
          <ReceiptPrintModal
            saleId={saleId}
            registerId={registerId}
            onClose={() => setPrintOpen(false)}
          />
        </Suspense>
      )}

      {externalPaymentConfirmation !== null ? (
        <Suspense fallback={null}>
          <ExternalPaymentEvidenceDialog
            open
            attemptId={externalPaymentConfirmation.attempt.id}
            method={externalPaymentConfirmation.method}
            amount={externalPaymentConfirmation.amount}
            currency={currency}
            canResolveDecline={canReconcileExternalPayment}
            error={topError}
            isLoading={
              beginPaymentAttemptReconciliation.isPending ||
              confirmPaymentAttempt.isPending ||
              voidPaymentAttempt.isPending
            }
            onConfirm={confirmExternalPayment}
            onDecline={cancelExternalPayment}
          />
        </Suspense>
      ) : null}

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
