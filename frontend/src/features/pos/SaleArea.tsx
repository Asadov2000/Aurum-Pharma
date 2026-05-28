import { useState } from "react";

import {
  Badge,
  Button,
  Input,
  Label,
  Select,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { CatalogPicker } from "@/features/catalog/CatalogPicker";
import { describeApiError } from "@/lib/errorMessages";

import {
  paymentMethodLabel,
  paymentMethodOptions,
  saleStatusLabel,
  saleStatusTone,
} from "./labels";
import { PrescriptionModal } from "./PrescriptionModal";
import {
  useAddPayment,
  useAddSaleItem,
  useCompleteSale,
  useCreateSale,
  useDeleteSaleItem,
  useSaleQuery,
} from "./queries";
import { type PaymentMethod, type SaleDetails } from "./types";

export function SaleArea({ registerId }: { registerId: string }): JSX.Element {
  const [saleId, setSaleId] = useState<string | null>(null);
  const [topError, setTopError] = useState<string | null>(null);
  const [prescriptionOpen, setPrescriptionOpen] = useState(false);
  const [requiresRx, setRequiresRx] = useState(false);

  const createSale = useCreateSale();

  const onStart = async () => {
    setTopError(null);
    try {
      const sale = await createSale.mutateAsync(registerId);
      setSaleId(sale.id);
      setRequiresRx(false);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось создать продажу"));
    }
  };

  if (saleId === null) {
    return (
      <div className="rounded-lg border border-dashed border-input bg-surface px-6 py-10 text-center">
        <p className="text-sm text-foreground-muted">Активной продажи нет</p>
        <Button className="mt-4" onClick={() => void onStart()} isLoading={createSale.isPending}>
          + Новая продажа
        </Button>
        {topError && <p className="mt-2 text-sm text-danger">{topError}</p>}
      </div>
    );
  }

  return (
    <ActiveSale
      saleId={saleId}
      onFinished={() => setSaleId(null)}
      requiresRx={requiresRx}
      setRequiresRx={setRequiresRx}
      prescriptionOpen={prescriptionOpen}
      setPrescriptionOpen={setPrescriptionOpen}
    />
  );
}

interface ActiveProps {
  saleId: string;
  onFinished: () => void;
  requiresRx: boolean;
  setRequiresRx: (v: boolean) => void;
  prescriptionOpen: boolean;
  setPrescriptionOpen: (v: boolean) => void;
}

function ActiveSale({
  saleId,
  onFinished,
  requiresRx,
  setRequiresRx,
  prescriptionOpen,
  setPrescriptionOpen,
}: ActiveProps): JSX.Element {
  const saleQuery = useSaleQuery(saleId);
  const addItem = useAddSaleItem();
  const deleteItem = useDeleteSaleItem();
  const addPayment = useAddPayment();
  const completeSale = useCompleteSale();

  const [pickerCatalogId, setPickerCatalogId] = useState("");
  const [qty, setQty] = useState("1");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("cash");
  const [paymentAmount, setPaymentAmount] = useState("");
  const [topError, setTopError] = useState<string | null>(null);

  if (saleQuery.isLoading || !saleQuery.data) {
    return <p className="text-sm text-foreground-muted">Загрузка продажи…</p>;
  }
  const sale: SaleDetails = saleQuery.data;
  const isDraft = sale.status === "draft";
  const totalPaid = sale.payments.reduce((sum, p) => sum + Number(p.amount), 0);
  const totalDue = Number(sale.total_amount);
  const remaining = totalDue - totalPaid;

  const onAddItem = async () => {
    if (!pickerCatalogId) {
      setTopError("Выберите позицию каталога");
      return;
    }
    if (Number(qty) <= 0) {
      setTopError("Количество должно быть больше 0");
      return;
    }
    setTopError(null);
    try {
      const res = await addItem.mutateAsync({ saleId, catalogId: pickerCatalogId, qty });
      if (res.requires_prescription_log) {
        setRequiresRx(true);
        setPrescriptionOpen(true);
      }
      setPickerCatalogId("");
      setQty("1");
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось добавить позицию"));
    }
  };

  const onDeleteItem = async (itemId: string) => {
    try {
      await deleteItem.mutateAsync({ saleId, itemId });
    } catch (err) {
      window.alert(describeApiError(err, "Не удалось удалить"));
    }
  };

  const onAddPayment = async () => {
    if (Number(paymentAmount) <= 0) {
      setTopError("Сумма должна быть больше 0");
      return;
    }
    setTopError(null);
    try {
      await addPayment.mutateAsync({
        saleId,
        payload: { payment_method: paymentMethod, amount: paymentAmount },
      });
      setPaymentAmount("");
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось добавить оплату"));
    }
  };

  const onComplete = async () => {
    if (requiresRx) {
      setPrescriptionOpen(true);
      return;
    }
    if (remaining > 0.001) {
      setTopError(`Осталось оплатить ${remaining.toFixed(2)} ${sale.currency}`);
      return;
    }
    setTopError(null);
    try {
      await completeSale.mutateAsync(saleId);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось завершить продажу"));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-medium text-foreground">
            Продажа{" "}
            <span className="font-mono text-sm text-foreground-muted">{sale.id.slice(0, 8)}</span>
          </h2>
          <Badge tone={saleStatusTone[sale.status]}>{saleStatusLabel[sale.status]}</Badge>
          {requiresRx && <Badge tone="warning">требуется рецепт</Badge>}
        </div>
        {!isDraft && (
          <Button variant="secondary" onClick={onFinished}>
            Новая продажа
          </Button>
        )}
      </div>

      {/* ITEMS */}
      {isDraft && (
        <div className="flex flex-wrap items-end gap-3 rounded-md border border-border bg-foreground/[0.03] p-3">
          <div className="min-w-[260px] flex-1">
            <Label>Позиция</Label>
            <CatalogPicker
              value={pickerCatalogId}
              onChange={(id) => setPickerCatalogId(id)}
              clearable
            />
          </div>
          <div className="w-28">
            <Label htmlFor="qty">Кол-во</Label>
            <Input
              id="qty"
              type="text"
              inputMode="decimal"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </div>
          <Button onClick={() => void onAddItem()} isLoading={addItem.isPending}>
            Добавить
          </Button>
        </div>
      )}

      {sale.items.length === 0 ? (
        <TableEmpty>В чеке пока нет позиций</TableEmpty>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>#</TH>
              <TH>Позиция</TH>
              <TH className="text-right">Кол-во</TH>
              <TH className="text-right">Цена</TH>
              <TH className="text-right">Сумма</TH>
              <TH></TH>
            </TR>
          </THead>
          <TBody>
            {sale.items.map((it) => (
              <TR key={it.id}>
                <TD>{it.position}</TD>
                <TD className="font-mono text-xs">{it.catalog_id.slice(0, 8)}</TD>
                <TD className="text-right font-mono">{it.qty}</TD>
                <TD className="text-right font-mono">{Number(it.unit_price).toFixed(2)}</TD>
                <TD className="text-right font-mono">{Number(it.total_price).toFixed(2)}</TD>
                <TD className="text-right">
                  {isDraft && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void onDeleteItem(it.id)}
                      isLoading={deleteItem.isPending}
                    >
                      ✕
                    </Button>
                  )}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      {/* PAYMENTS */}
      <div className="rounded-md border border-border bg-surface p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-foreground-secondary">Оплаты</h3>
          <div className="text-right">
            <p className="text-xs text-foreground-muted">К оплате</p>
            <p className="font-mono text-lg">
              {totalDue.toFixed(2)} {sale.currency}
            </p>
            {totalPaid > 0 && (
              <p className="text-xs text-foreground-muted">
                оплачено {totalPaid.toFixed(2)}; остаток {remaining.toFixed(2)}
              </p>
            )}
          </div>
        </div>

        {sale.payments.length > 0 && (
          <ul className="mt-2 divide-y divide-border text-sm">
            {sale.payments.map((p) => (
              <li key={p.id} className="flex justify-between py-1.5">
                <span>{paymentMethodLabel[p.payment_method]}</span>
                <span className="font-mono">
                  {Number(p.amount).toFixed(2)} {p.currency}
                </span>
              </li>
            ))}
          </ul>
        )}

        {isDraft && sale.items.length > 0 && (
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <div className="w-44">
              <Label htmlFor="method">Способ</Label>
              <Select
                id="method"
                value={paymentMethod}
                onChange={(e) => setPaymentMethod(e.target.value as PaymentMethod)}
              >
                {paymentMethodOptions.map((m) => (
                  <option key={m} value={m}>
                    {paymentMethodLabel[m]}
                  </option>
                ))}
              </Select>
            </div>
            <div className="w-32">
              <Label htmlFor="pay_amount">Сумма</Label>
              <Input
                id="pay_amount"
                type="text"
                inputMode="decimal"
                value={paymentAmount}
                onChange={(e) => setPaymentAmount(e.target.value)}
                placeholder={remaining > 0 ? remaining.toFixed(2) : "0"}
              />
            </div>
            <Button
              variant="secondary"
              onClick={() => void onAddPayment()}
              isLoading={addPayment.isPending}
            >
              + Оплата
            </Button>
          </div>
        )}
      </div>

      {topError && <p className="text-sm text-danger">{topError}</p>}

      {isDraft && (
        <div className="flex justify-end">
          <Button
            onClick={() => void onComplete()}
            isLoading={completeSale.isPending}
            disabled={sale.items.length === 0}
          >
            Завершить продажу
          </Button>
        </div>
      )}

      {!isDraft && sale.receipt_number && (
        <p className="text-sm text-success-foreground">
          ✅ Чек № {sale.receipt_number} оформлен. Партии списаны.
        </p>
      )}

      <PrescriptionModal
        saleId={saleId}
        open={prescriptionOpen}
        onClose={() => setPrescriptionOpen(false)}
        onSaved={() => {
          setRequiresRx(false);
          setPrescriptionOpen(false);
        }}
      />
    </div>
  );
}
