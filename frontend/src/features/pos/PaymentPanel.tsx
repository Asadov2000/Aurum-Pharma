import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import { paymentMethodLabel, paymentMethodOptions } from "./labels";
import { type Payment, type PaymentMethod } from "./types";

const tileIcon: Record<PaymentMethod, string> = {
  cash: "💵",
  card: "💳",
  bank_transfer: "🏦",
};

/**
 * Right-column payment summary: the big "К ОПЛАТЕ" figure, one-tap payment
 * tiles (each pays the remaining balance in that method), the recorded
 * payments, and the full-width "Завершить" action.
 */
export function PaymentPanel({
  totalDue,
  totalPaid,
  remaining,
  currency,
  payments,
  isDraft,
  completing,
  payingMethod,
  onPayTile,
  onComplete,
  completedReceiptNumber,
  onPrint,
  onNewSale,
  newSaleHint,
  touch,
  completeHint,
}: {
  totalDue: number;
  totalPaid: number;
  remaining: number;
  currency: string;
  payments: Payment[];
  isDraft: boolean;
  completing: boolean;
  payingMethod: PaymentMethod | null;
  onPayTile: (method: PaymentMethod) => void;
  onComplete: () => void;
  completedReceiptNumber: string | null;
  onPrint?: () => void;
  onNewSale?: () => void;
  newSaleHint?: string;
  touch?: boolean;
  completeHint?: string;
}): JSX.Element {
  const settled = remaining <= 0.001;

  return (
    <div className="flex flex-col gap-4">
      {/* К ОПЛАТЕ */}
      <div className="rounded-xl border border-border bg-surface p-5 text-center shadow-sm">
        <p className="text-xs uppercase tracking-wide text-foreground-muted">К оплате</p>
        <p className="mt-1 font-mono text-5xl font-bold tabular-nums text-primary lg:text-6xl">
          {totalDue.toFixed(2)}
        </p>
        <p className="text-sm text-foreground-muted">{currency}</p>
        {totalDue > 0 && (
          <div className="mt-3 flex justify-center gap-6 text-sm">
            <span className="text-foreground-secondary">
              Оплачено{" "}
              <span className="font-mono tabular-nums text-foreground">
                {totalPaid.toFixed(2)}
              </span>
            </span>
            <span className={cn(settled ? "text-success-foreground" : "text-warning-foreground")}>
              Остаток{" "}
              <span className="font-mono tabular-nums">{Math.max(0, remaining).toFixed(2)}</span>
            </span>
          </div>
        )}
      </div>

      {/* Payment tiles */}
      {isDraft && (
        <div className="grid grid-cols-3 gap-2">
          {paymentMethodOptions.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => onPayTile(m)}
              disabled={settled || payingMethod !== null || totalDue <= 0}
              className={cn(
                "pos-tile flex flex-col items-center justify-center gap-1 rounded-xl border border-border bg-surface p-2 text-center transition-colors",
                touch ? "min-h-[96px]" : "min-h-[88px]",
                "hover:border-primary hover:bg-primary/5 active:bg-primary/10",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <span className={cn(touch ? "text-3xl" : "text-2xl")} aria-hidden="true">
                {tileIcon[m]}
              </span>
              <span
                className={cn(
                  "font-medium text-foreground",
                  touch ? "text-base" : "text-sm",
                )}
              >
                {paymentMethodLabel[m]}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Recorded payments */}
      {payments.length > 0 && (
        <ul className="divide-y divide-border rounded-xl border border-border bg-surface text-sm">
          {payments.map((p) => (
            <li key={p.id} className="flex items-center justify-between px-4 py-2">
              <span className="text-foreground-secondary">{paymentMethodLabel[p.payment_method]}</span>
              <span className="font-mono tabular-nums text-foreground">
                {Number(p.amount).toFixed(2)} {p.currency}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Complete */}
      {isDraft && (
        <Button
          size="xl"
          className="w-full"
          onClick={onComplete}
          isLoading={completing}
          disabled={totalDue <= 0}
          title={completeHint}
        >
          Завершить продажу
        </Button>
      )}

      {!isDraft && completedReceiptNumber && (
        <div className="space-y-3 rounded-xl border border-success/40 bg-success-subtle p-4 text-center">
          <p className="font-medium text-success-foreground">
            ✅ Чек № {completedReceiptNumber} оформлен
          </p>
          {onPrint && (
            <Button size="xl" variant="secondary" className="w-full" onClick={onPrint}>
              🖨 Печать чека
            </Button>
          )}
          {onNewSale && (
            <Button size="xl" className="w-full" onClick={onNewSale} title={newSaleHint}>
              + Новая продажа
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
