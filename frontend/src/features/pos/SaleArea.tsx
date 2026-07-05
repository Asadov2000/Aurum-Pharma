import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { isAxiosError } from "axios";

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
import { type PaymentMethod, type SaleDetails } from "./types";
import { type PosMode } from "./usePosMode";

// Lazy so the on-screen keypad chunk only loads when a cashier taps a field.
const NumPad = lazy(() => import("./NumPad"));

type NumPadState =
  | { kind: "qty"; itemId: string; initial: string }
  | { kind: "payment"; method: PaymentMethod; initial: string };

type FlashTone = "success" | "danger";

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

  const clearDraft = useCallback(() => clearDraftStorage(registerId), [registerId]);

  // Persist the live draft so a reload (or accidental close) restores the cart.
  // The savedAt stamp refreshes on every change → the TTL is an idle timeout.
  useEffect(() => {
    if (saleId && isDraft) saveDraft(registerId, saleId, nameById);
  }, [saleId, nameById, isDraft, registerId]);

  // If a restored sale turns out already completed/voided, drop the stash so it
  // doesn't keep resurrecting on reload.
  useEffect(() => {
    if (sale && sale.status !== "draft") clearDraft();
  }, [sale, clearDraft]);

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
    if (!saleId) return;
    setTopError(null);
    try {
      await updateItem.mutateAsync({ saleId, itemId, qty: String(qty) });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось изменить количество"));
    }
  };

  const onDelete = async (itemId: string) => {
    if (!saleId) return;
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
    setTopError(null);
    setPayingMethod(method);
    try {
      await addPayment.mutateAsync({
        saleId,
        payload: { payment_method: method, amount: amt.toFixed(2) },
      });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось добавить оплату"));
    } finally {
      setPayingMethod(null);
    }
  };

  // Touch: tapping a tile opens the keypad pre-filled with the remaining amount
  // (so partial payments are easy). Keyboard/desktop: one tap pays the rest.
  const onPayTile = (method: PaymentMethod) => {
    if (!saleId || remaining <= 0.001) return;
    if (touch) {
      setNumpad({ kind: "payment", method, initial: remaining.toFixed(2) });
      return;
    }
    void pay(method, remaining.toFixed(2));
  };

  const onComplete = async () => {
    if (!saleId || completingRef.current || completeSale.isPending) return;
    if (requiresRx) {
      setPrescriptionOpen(true);
      return;
    }
    if (remaining > 0.001) {
      setTopError(`Осталось оплатить ${remaining.toFixed(2)} ${currency}`);
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
      clearDraft();
    } catch (err) {
      completingRef.current = false;
      setTopError(describeApiError(err, "Не удалось завершить продажу"));
    }
  };

  const onNewSale = () => {
    completingRef.current = false;
    clearDraft();
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
      <BarcodeListener enabled={isDraft} onScan={(code) => void onScan(code)} />

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
            Прошлый черновик устарел (более {draftTtlMin} мин) и был очищен — начните новую
            продажу.
          </p>
        )}

        {isDraft && (
          <SearchBar
            ref={searchRef}
            onAdd={onAdd}
            busy={addItem.isPending}
            touch={touch}
            branchId={branchId ?? undefined}
          />
        )}

        <CartList
          items={items}
          nameById={nameById}
          currency={currency}
          editable={isDraft}
          onQtyChange={(id, q) => void onQtyChange(id, q)}
          onDelete={(id) => void onDelete(id)}
          onQtyTap={touch ? onQtyTap : undefined}
          touch={touch}
          busy={updateItem.isPending || deleteItem.isPending}
        />

        {topError && <p className="text-sm text-danger">{topError}</p>}
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
            payingMethod={payingMethod}
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
