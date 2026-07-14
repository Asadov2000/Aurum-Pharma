import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { isAxiosError } from "axios";

import { Button } from "@/components/ui";
import { findByBarcode } from "@/features/catalog/api";
import { requestDesktopCashDrawerOpen } from "@/lib/desktopBridge";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { BarcodeListener } from "./BarcodeListener";
import { CartList } from "./CartList";
import { PaymentPanel } from "./PaymentPanel";
import { PrescriptionModal } from "./PrescriptionModal";
import { ReceiptPrintModal } from "./ReceiptPrintModal";
import { SearchBar } from "./SearchBar";
import { ShiftBar } from "./ShiftBar";
import { beep } from "./beep";
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
  useAddPayment,
  useAddSaleItem,
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
import { type PaymentMethod, type SaleDetails } from "./types";
import { type PosMode } from "./usePosMode";

// Lazy so the on-screen keypad chunk only loads when a cashier taps a field.
const NumPad = lazy(() => import("./NumPad"));

type NumPadState =
  | { kind: "qty"; itemId: string; initial: string }
  | { kind: "payment"; method: PaymentMethod; initial: string };

type FlashTone = "success" | "danger";

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

/**
 * The POS workspace. Owns the shift gate and the active sale, and lays the UI
 * out responsively: two columns on ≥lg (cart left, payment right) and a single
 * stack below lg. `mode` decides touch- vs keyboard-optimised behaviour.
 */
export function SaleArea({
  registerId,
  mode,
  soundOn,
  draftTtlMin,
}: {
  registerId: string;
  mode: PosMode;
  soundOn: boolean;
  draftTtlMin: number;
}): JSX.Element {
  const shiftQuery = useCurrentShiftQuery(registerId);
  const hasShift = Boolean(shiftQuery.data);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
      {hasShift ? (
        // Key by register so switching registers restores that one's draft.
        <ActiveWorkspace
          key={registerId}
          registerId={registerId}
          branchId={shiftQuery.data?.branch_id ?? null}
          mode={mode}
          soundOn={soundOn}
          draftTtlMin={draftTtlMin}
        />
      ) : (
        <>
          <div className="lg:col-span-7">
            <div className="flex h-full min-h-[200px] items-center justify-center rounded-xl border border-dashed border-border bg-surface p-8 text-center text-foreground-muted">
              Откройте смену, чтобы начать продажу →
            </div>
          </div>
          <div className="lg:col-span-5">
            <ShiftBar registerId={registerId} mode={mode} />
          </div>
        </>
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
}: {
  registerId: string;
  branchId: string | null;
  mode: PosMode;
  soundOn: boolean;
  draftTtlMin: number;
}): JSX.Element {
  const touch = mode === "touch";
  const keyboard = mode === "keyboard";

  const [init] = useState<DraftInit>(() => loadDraft(registerId, draftTtlMin));
  const [saleId, setSaleId] = useState<string | null>(init.saleId);
  const [nameById, setNameById] = useState<Record<string, string>>(init.nameById);
  const [pendingPayment, setPendingPayment] = useState<PendingPaymentOperation | null>(() =>
    init.saleId ? loadPendingPaymentOperation(init.saleId) : null,
  );
  const [completionUncertain, setCompletionUncertain] = useState(
    () => init.saleId !== null && hasPendingCompletion(init.saleId),
  );
  const [staleNotice, setStaleNotice] = useState<boolean>(init.expired);
  const [topError, setTopError] = useState<string | null>(null);
  const [prescriptionOpen, setPrescriptionOpen] = useState(false);
  const [requiresRx, setRequiresRx] = useState(false);
  const [payingMethod, setPayingMethod] = useState<PaymentMethod | null>(null);
  const [numpad, setNumpad] = useState<NumPadState | null>(null);
  const [flash, setFlash] = useState<FlashTone | null>(null);
  const [printOpen, setPrintOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const flashTimer = useRef<number | undefined>(undefined);
  const completingRef = useRef(false);

  const createSale = useCreateSale();
  const addItem = useAddSaleItem();
  const updateItem = useUpdateSaleItem();
  const deleteItem = useDeleteSaleItem();
  const addPayment = useAddPayment();
  const completeSale = useCompleteSale();
  const saleQuery = useSaleQuery(saleId);

  const sale: SaleDetails | null = saleQuery.data ?? null;
  const isDraft = !sale || sale.status === "draft";
  const items = sale?.items ?? [];
  const payments = sale?.payments ?? [];
  const currency = sale?.currency ?? "TJS";
  const totalDue = sale ? Number(sale.total_amount) : 0;
  const totalPaid = payments.reduce((sum, p) => sum + Number(p.amount), 0);
  const remaining = totalDue - totalPaid;
  const saleEditingBlocked =
    completeSale.isPending ||
    completionUncertain ||
    pendingPayment !== null ||
    (saleId !== null && sale === null);

  const clearDraft = useCallback(() => clearDraftStorage(registerId), [registerId]);
  const persistCompletedReceipt = useCallback(
    (completedSaleId: string): boolean => {
      const persisted = saveDraft(registerId, completedSaleId, nameById, "completed");
      if (persisted) {
        clearPendingCompletion(completedSaleId);
        setCompletionUncertain(false);
        setTopError(null);
        return true;
      }

      setCompletionUncertain(true);
      setTopError(
        "Чек завершён, но не удалось сохранить ссылку для восстановления. Не закрывайте приложение и повторите сверку.",
      );
      return false;
    },
    [registerId, nameById],
  );

  // Persist the live draft so a reload (or accidental close) restores the cart.
  // The savedAt stamp refreshes on every change → the TTL is an idle timeout.
  useEffect(() => {
    if (sale?.status === "draft") saveDraft(registerId, sale.id, nameById, "draft");
  }, [sale, nameById, registerId]);

  // Keep the completed receipt addressable until the cashier explicitly starts
  // another sale. This makes printer/browser recovery deterministic.
  useEffect(() => {
    if (sale && sale.status !== "draft") {
      persistCompletedReceipt(sale.id);
    }
  }, [sale, persistCompletedReceipt]);

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

  const doFlash = (tone: FlashTone) => {
    setFlash(tone);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(null), 600);
  };

  // Lazily create a draft on the first add so we never leave empty drafts.
  const ensureSaleId = useCallback(async (): Promise<string> => {
    if (saleId) return saleId;
    const created = await createSale.mutateAsync(registerId);
    setSaleId(created.id);
    setStaleNotice(false);
    return created.id;
  }, [saleId, createSale, registerId]);

  const onAdd = async (catalogId: string, name: string, qty: number) => {
    if (saleEditingBlocked) return;
    setTopError(null);
    try {
      const id = await ensureSaleId();
      if (name) setNameById((m) => ({ ...m, [catalogId]: name }));
      const res = await addItem.mutateAsync({ saleId: id, catalogId, qty: String(qty) });
      if (res.requires_prescription_log) {
        setRequiresRx(true);
        setPrescriptionOpen(true);
      }
      searchRef.current?.focus();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось добавить позицию"));
    }
  };

  const onScan = async (code: string) => {
    setTopError(null);
    try {
      const item = await findByBarcode(code);
      await onAdd(item.id, item.brand_name, 1);
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

  const onQtyChange = async (itemId: string, qty: number) => {
    if (!saleId || saleEditingBlocked) return;
    setTopError(null);
    try {
      await updateItem.mutateAsync({ saleId, itemId, qty: String(qty) });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось изменить количество"));
    }
  };

  const onDelete = async (itemId: string) => {
    if (!saleId || saleEditingBlocked) return;
    try {
      await deleteItem.mutateAsync({ saleId, itemId });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось удалить"));
    }
  };

  const pay = async (method: PaymentMethod, amount: string) => {
    if (!saleId) return;
    const amt = Number(amount);
    if (!(amt > 0)) return;
    const normalizedAmount = amt.toFixed(2);
    const storedOperation =
      pendingPayment?.saleId === saleId ? pendingPayment : loadPendingPaymentOperation(saleId);
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

    if (!saveDraft(registerId, saleId, nameById, "draft")) {
      setTopError(
        "Локальное хранилище кассы недоступно. Оплата не отправлена; освободите место или перезапустите приложение.",
      );
      return;
    }
    const operation =
      storedOperation ?? createPendingPaymentOperation(saleId, method, normalizedAmount);
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

  // Touch: tapping a tile opens the keypad pre-filled with the remaining amount
  // (so partial payments are easy). Keyboard/desktop: one tap pays the rest.
  const onPayTile = (method: PaymentMethod) => {
    if (!saleId || remaining <= 0.001) return;
    if (completionUncertain || completeSale.isPending) return;
    if (pendingPayment) {
      if (pendingPayment.paymentMethod !== method) {
        setTopError(
          "Результат предыдущей оплаты ещё не подтверждён. Повторите её тем же способом.",
        );
        return;
      }
      void pay(method, pendingPayment.amount);
      return;
    }
    if (touch) {
      setNumpad({ kind: "payment", method, initial: remaining.toFixed(2) });
      return;
    }
    void pay(method, remaining.toFixed(2));
  };

  const onComplete = async () => {
    if (!saleId || completingRef.current || completeSale.isPending) return;
    if (pendingPayment || addPayment.isPending) {
      setTopError("Сначала подтвердите результат оплаты.");
      return;
    }
    if (requiresRx) {
      setPrescriptionOpen(true);
      return;
    }
    if (remaining > 0.001) {
      setTopError(`Осталось оплатить ${remaining.toFixed(2)} ${currency}`);
      return;
    }
    if (!saveDraft(registerId, saleId, nameById, "draft")) {
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

  const onNewSale = () => {
    if (
      completingRef.current ||
      completeSale.isPending ||
      addPayment.isPending ||
      pendingPayment ||
      completionUncertain
    ) {
      setTopError("Сначала подтвердите результат текущей денежной операции.");
      return;
    }
    if (!clearDraft()) {
      setTopError(
        "Не удалось очистить локальное состояние кассы. Новая продажа не начата; перезапустите приложение.",
      );
      return;
    }
    completingRef.current = false;
    if (saleId) {
      clearPendingPaymentOperation(saleId);
      clearPendingCompletion(saleId);
    }
    setSaleId(null);
    setNameById({});
    setRequiresRx(false);
    setTopError(null);
    setStaleNotice(false);
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
      void pay(numpad.method, value);
    }
    setNumpad(null);
  };

  // Enter on a ready receipt pays the remaining balance in cash — the most
  // common tender — in one keystroke (F3→Enter no longer required). F3/F4 and
  // the sums/logic are unchanged; the handler below guards it so it never fires
  // while typing in a field or with a dialog open.
  const onEnterPayCash = () => {
    if (isDraft && totalDue > 0 && remaining > 0.001) onPayTile("cash");
  };

  // Keyboard shortcuts. Ref holds the latest handlers so we bind the listener
  // once. F-keys are ignored while any modal/keypad (role="dialog") is open so
  // they don't fire actions hidden behind it.
  const actionsRef = useRef({ onNewSale, onComplete, onEnterPayCash });
  actionsRef.current = { onNewSale, onComplete, onEnterPayCash };

  useEffect(() => {
    if (!keyboard) return;
    const handler = (e: KeyboardEvent) => {
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
      switch (e.key) {
        case "F2":
          if (dialogOpen) return;
          e.preventDefault();
          actionsRef.current.onNewSale();
          searchRef.current?.focus();
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
          if (dialogOpen || fieldHasContent) return;
          e.preventDefault();
          actionsRef.current.onEnterPayCash();
          break;
        case "Escape":
          if (!dialogOpen) setTopError(null);
          break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [keyboard]);

  return (
    <>
      {/* Global barcode capture — only while editing a draft. */}
      <BarcodeListener
        enabled={isDraft && !saleEditingBlocked}
        onScan={(code) => void onScan(code)}
      />

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

      {/* Shift status strip — full width on top, so the selling columns get
          the space (it lived in the right column before). */}
      <div className="lg:col-span-12">
        <ShiftBar registerId={registerId} mode={mode} />
      </div>

      {/* LEFT — search + cart */}
      <div className="space-y-3 lg:col-span-7">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-foreground">
            Чек{" "}
            {sale?.receipt_number && (
              <span className="font-mono text-sm text-foreground-muted">
                № {sale.receipt_number}
              </span>
            )}
          </h2>
          {requiresRx && (
            <span className="rounded-full bg-warning-subtle px-2.5 py-0.5 text-xs font-medium text-warning-foreground ring-1 ring-inset ring-warning/30">
              требуется рецепт
            </span>
          )}
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
              busy={addItem.isPending}
              touch={touch}
              branchId={branchId ?? undefined}
            />
          </fieldset>
        )}

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
        />

        {topError && (
          <div className="flex flex-wrap items-center gap-2 text-sm text-danger">
            <p>{topError}</p>
            {(pendingPayment || completionUncertain) && (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                isLoading={saleQuery.isFetching}
                onClick={() => void saleQuery.refetch()}
              >
                Сверить с сервером
              </Button>
            )}
          </div>
        )}
      </div>

      {/* RIGHT — payment, sticky so it's always in view */}
      <div className="lg:col-span-5">
        <div className="space-y-4 lg:sticky lg:top-4">
          <PaymentPanel
            totalDue={totalDue}
            totalPaid={totalPaid}
            remaining={remaining}
            currency={currency}
            payments={payments}
            isDraft={isDraft}
            completing={completeSale.isPending}
            completionUncertain={completionUncertain}
            payingMethod={payingMethod}
            pendingPaymentMethod={pendingPayment?.paymentMethod ?? null}
            onPayTile={onPayTile}
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

      {saleId && (
        <PrescriptionModal
          saleId={saleId}
          open={prescriptionOpen}
          onClose={() => setPrescriptionOpen(false)}
          onSaved={() => {
            setRequiresRx(false);
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
    </>
  );
}
