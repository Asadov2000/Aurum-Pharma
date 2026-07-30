import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import { paymentMethodLabel, paymentMethodOptions } from "./labels";
import { type Payment, type PaymentMethod } from "./types";

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
  completionUncertain,
  payingMethod,
  pendingPaymentMethod,
  onPayTile,
  onClearPayments,
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
  completionUncertain: boolean;
  payingMethod: PaymentMethod | null;
  pendingPaymentMethod: PaymentMethod | null;
  onPayTile: (method: PaymentMethod) => void;
  onClearPayments?: () => void;
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
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-3">
      {/* К ОПЛАТЕ */}
      <div className="border-b border-border px-1 pb-4 pt-1">
        <div className="flex items-end justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground-secondary">К оплате</p>
            {totalDue > 0 && (
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                <span className="text-foreground-secondary">
                  Оплачено{" "}
                  <span className="font-mono tabular-nums text-foreground">
                    {totalPaid.toFixed(2)}
                  </span>
                </span>
                <span
                  className={cn(settled ? "text-success-foreground" : "text-warning-foreground")}
                >
                  Остаток{" "}
                  <span className="font-mono tabular-nums">
                    {Math.max(0, remaining).toFixed(2)}
                  </span>
                </span>
              </div>
            )}
          </div>
          <p className="shrink-0 text-right font-mono text-4xl font-bold tabular-nums text-foreground">
            {totalDue.toFixed(2)}{" "}
            <span className="font-sans text-sm font-semibold text-foreground-secondary">
              {currency}
            </span>
          </p>
        </div>
      </div>

      {/* Payment tiles */}
      {isDraft && (
        <div className="grid grid-cols-3 gap-2">
          {paymentMethodOptions.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => onPayTile(m)}
              disabled={
                settled ||
                payingMethod !== null ||
                totalDue <= 0 ||
                completing ||
                completionUncertain ||
                (pendingPaymentMethod !== null && pendingPaymentMethod !== m)
              }
              className={cn(
                "pos-tile flex flex-col items-center justify-center gap-2 rounded-lg border border-border bg-surface p-2 text-center transition-colors duration-fast",
                touch ? "min-h-[92px]" : "min-h-20",
                "hover:border-primary hover:bg-primary/5 active:bg-primary/10",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <PaymentMethodIcon method={m} />
              <span className={cn("font-medium text-foreground", touch ? "text-base" : "text-sm")}>
                {paymentMethodLabel[m]}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Recorded payments */}
      {payments.length > 0 && (
        <div className="space-y-2">
          <ul className="divide-y divide-border border-y border-border text-sm">
            {payments.map((p) => (
              <li key={p.id} className="flex items-center justify-between px-4 py-2">
                <span className="text-foreground-secondary">
                  {paymentMethodLabel[p.payment_method]}
                </span>
                <span className="font-mono tabular-nums text-foreground">
                  {Number(p.amount).toFixed(2)} {p.currency}
                </span>
              </li>
            ))}
          </ul>
          {isDraft && onClearPayments && (
            <Button type="button" size="sm" variant="ghost" onClick={onClearPayments}>
              Очистить оплату
            </Button>
          )}
        </div>
      )}

      {/* Complete */}
      {isDraft && (
        <Button
          size="xl"
          variant="success"
          className="w-full"
          onClick={onComplete}
          isLoading={completing}
          disabled={totalDue <= 0 || pendingPaymentMethod !== null}
          title={completeHint}
        >
          <CheckIcon />
          Завершить продажу
        </Button>
      )}

      {!isDraft && completedReceiptNumber && (
        <div className="space-y-3 rounded-lg border border-success/40 bg-success-subtle p-4 text-center">
          <p className="font-medium text-success-foreground">
            Чек № {completedReceiptNumber} оформлен
          </p>
          {onPrint && (
            <Button size="xl" variant="secondary" className="w-full" onClick={onPrint}>
              Печать чека
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

function PaymentMethodIcon({ method }: { method: PaymentMethod }): JSX.Element {
  if (method === "bank_transfer") {
    return (
      <svg
        aria-hidden="true"
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-primary"
      >
        <path d="M4 8h14" />
        <path d="m15 5 3 3-3 3" />
        <path d="M20 16H6" />
        <path d="m9 13-3 3 3 3" />
      </svg>
    );
  }

  return (
    <span className="relative block h-5 w-8 rounded-sm border-2 border-primary" aria-hidden="true">
      {method === "cash" ? (
        <span className="absolute left-1/2 top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary" />
      ) : (
        <span className="absolute inset-x-0 top-1 h-0.5 bg-primary" />
      )}
    </span>
  );
}

function CheckIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m5 12 4 4L19 6" />
    </svg>
  );
}
