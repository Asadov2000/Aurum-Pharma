import { useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { CartList } from "./CartList";
import { PaymentPanel } from "./PaymentPanel";
import { PrescriptionModal } from "./PrescriptionModal";
import { SearchBar } from "./SearchBar";
import { ShiftBar } from "./ShiftBar";
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

/**
 * The POS workspace. Owns the shift gate and the active sale, and lays the UI
 * out responsively: two columns on ≥lg (cart left, payment right) and a single
 * stack below lg.
 */
export function SaleArea({ registerId }: { registerId: string }): JSX.Element {
  const shiftQuery = useCurrentShiftQuery(registerId);
  const hasShift = Boolean(shiftQuery.data);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
      {hasShift ? (
        <ActiveWorkspace registerId={registerId} />
      ) : (
        <>
          <div className="lg:col-span-7">
            <div className="flex h-full min-h-[200px] items-center justify-center rounded-xl border border-dashed border-border bg-surface p-8 text-center text-foreground-muted">
              Откройте смену, чтобы начать продажу →
            </div>
          </div>
          <div className="lg:col-span-5">
            <ShiftBar registerId={registerId} />
          </div>
        </>
      )}
    </div>
  );
}

function ActiveWorkspace({ registerId }: { registerId: string }): JSX.Element {
  const [saleId, setSaleId] = useState<string | null>(null);
  const [nameById, setNameById] = useState<Record<string, string>>({});
  const [topError, setTopError] = useState<string | null>(null);
  const [prescriptionOpen, setPrescriptionOpen] = useState(false);
  const [requiresRx, setRequiresRx] = useState(false);
  const [payingMethod, setPayingMethod] = useState<PaymentMethod | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

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

  // Lazily create a draft on the first add so we never leave empty drafts.
  const ensureSaleId = useCallback(async (): Promise<string> => {
    if (saleId) return saleId;
    const created = await createSale.mutateAsync(registerId);
    setSaleId(created.id);
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

  const onPayTile = async (method: PaymentMethod) => {
    if (!saleId || remaining <= 0.001) return;
    setTopError(null);
    setPayingMethod(method);
    try {
      await addPayment.mutateAsync({
        saleId,
        payload: { payment_method: method, amount: remaining.toFixed(2) },
      });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось добавить оплату"));
    } finally {
      setPayingMethod(null);
    }
  };

  const onComplete = async () => {
    if (!saleId) return;
    if (requiresRx) {
      setPrescriptionOpen(true);
      return;
    }
    if (remaining > 0.001) {
      setTopError(`Осталось оплатить ${remaining.toFixed(2)} ${currency}`);
      return;
    }
    setTopError(null);
    try {
      await completeSale.mutateAsync(saleId);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось завершить продажу"));
    }
  };

  const onNewSale = () => {
    setSaleId(null);
    setNameById({});
    setRequiresRx(false);
    setTopError(null);
  };

  return (
    <>
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
          <div className="flex items-center gap-2">
            {requiresRx && (
              <span className="rounded-full bg-warning-subtle px-2 py-0.5 text-xs font-medium text-warning-foreground">
                требуется рецепт
              </span>
            )}
            {!isDraft && (
              <Button variant="secondary" onClick={onNewSale}>
                + Новая продажа
              </Button>
            )}
          </div>
        </div>

        {isDraft && <SearchBar ref={searchRef} onAdd={onAdd} busy={addItem.isPending} />}

        <CartList
          items={items}
          nameById={nameById}
          currency={currency}
          editable={isDraft}
          onQtyChange={(id, q) => void onQtyChange(id, q)}
          onDelete={(id) => void onDelete(id)}
          busy={updateItem.isPending || deleteItem.isPending}
        />

        {topError && <p className="text-sm text-danger">{topError}</p>}
      </div>

      {/* RIGHT — shift + payment */}
      <div className="space-y-4 lg:col-span-5">
        <ShiftBar registerId={registerId} />
        <PaymentPanel
          totalDue={totalDue}
          totalPaid={totalPaid}
          remaining={remaining}
          currency={currency}
          payments={payments}
          isDraft={isDraft}
          completing={completeSale.isPending}
          payingMethod={payingMethod}
          onPayTile={(m) => void onPayTile(m)}
          onComplete={() => void onComplete()}
          completedReceiptNumber={!isDraft ? (sale?.receipt_number ?? null) : null}
        />
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
    </>
  );
}
