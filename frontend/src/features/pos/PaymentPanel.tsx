import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import { paymentMethodLabel, paymentMethodOptions } from "./labels";
import { type Payment, type PaymentMethod, type PaymentMethodRead } from "./types";

const cashKeys = [
  "7",
  "8",
  "9",
  "backspace",
  "4",
  "5",
  "6",
  "+50",
  "1",
  "2",
  "3",
  "+100",
  ",",
  "0",
  "00",
  "clear",
] as const;

type CashKey = (typeof cashKeys)[number];

/**
 * Right-column payment workspace. Payment recording stays in SaleArea; this
 * component presents the amount, tender methods, cash received and change.
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
  paymentMethods,
  mixedPaymentEnabled,
  paymentSettingsLoading,
  onPayTile,
  onRetryPendingPayment,
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
  payingMethod: PaymentMethodRead | null;
  pendingPaymentMethod: PaymentMethodRead | null;
  paymentMethods: PaymentMethod[];
  mixedPaymentEnabled: boolean;
  paymentSettingsLoading: boolean;
  onPayTile: (method: PaymentMethod, amount?: string) => void;
  onRetryPendingPayment?: () => void;
  onClearPayments?: () => void;
  onComplete: () => void;
  completedReceiptNumber: string | null;
  onPrint?: () => void;
  onNewSale?: () => void;
  newSaleHint?: string;
  touch?: boolean;
  completeHint?: string;
}): JSX.Element {
  const [activeMethod, setActiveMethod] = useState<PaymentMethod>(
    () => paymentMethods[0] ?? "cash",
  );
  const [cashReceived, setCashReceived] = useState("");
  const paymentMethodsKey = paymentMethods.join("|");
  const recordedMethod = payments[0]?.payment_method;
  const lockedMethod =
    !mixedPaymentEnabled &&
    recordedMethod !== undefined &&
    isCurrentPaymentMethod(recordedMethod) &&
    paymentMethods.includes(recordedMethod)
      ? recordedMethod
      : null;
  const fallbackMethod = lockedMethod ?? paymentMethods[0] ?? "cash";
  const selectedMethod =
    paymentMethods.includes(activeMethod) && (lockedMethod === null || activeMethod === lockedMethod)
      ? activeMethod
      : fallbackMethod;
  const settled = remaining <= 0.001;
  const cashReceivedNumber = parseCash(cashReceived);
  const nonCashPaid = payments.reduce(
    (sum, payment) => (payment.payment_method === "cash" ? sum : sum + Number(payment.amount)),
    0,
  );
  const cashPaid = payments.reduce(
    (sum, payment) => (payment.payment_method === "cash" ? sum + Number(payment.amount) : sum),
    0,
  );
  const cashDue = Math.max(0, totalDue - nonCashPaid);
  const change = Math.max(0, cashReceivedNumber - cashDue);
  const cashTenderInsufficient =
    isDraft &&
    selectedMethod === "cash" &&
    remaining > 0.001 &&
    cashReceivedNumber + 0.001 < remaining;
  const pendingMethodVisible =
    pendingPaymentMethod !== null &&
    isCurrentPaymentMethod(pendingPaymentMethod) &&
    paymentMethods.includes(pendingPaymentMethod);

  useEffect(() => {
    setActiveMethod((current) =>
      paymentMethods.includes(current) && (lockedMethod === null || current === lockedMethod)
        ? current
        : fallbackMethod,
    );
  }, [fallbackMethod, lockedMethod, paymentMethods, paymentMethodsKey]);

  useEffect(() => {
    if (!isDraft || totalDue <= 0 || payments.length > 0) return;
    setCashReceived(totalDue.toFixed(2));
  }, [isDraft, payments.length, totalDue]);

  const chooseMethod = (method: PaymentMethod) => {
    setActiveMethod(method);
    if (paymentSettingsLoading) return;
    const amount =
      method === "cash"
        ? Math.min(Math.max(0, cashReceivedNumber - cashPaid), remaining)
        : Math.max(0, remaining);
    if (!mixedPaymentEnabled && method === "cash" && amount + 0.001 < remaining) return;
    if (amount > 0) onPayTile(method, amount.toFixed(2));
  };

  const pressCashKey = (key: CashKey) => {
    setCashReceived((current) => applyCashKey(current, key));
  };

  return (
    <section
      aria-labelledby="payment-panel-title"
      className="flex min-h-[30rem] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-surface xl:h-[36rem]"
    >
      <header className="border-b border-border px-4 py-4">
        <div className="flex items-end justify-between gap-4">
          <div className="min-w-0">
            <h2
              id="payment-panel-title"
              className="text-sm font-semibold text-foreground-secondary"
            >
              К оплате
            </h2>
            {totalDue > 0 ? (
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                <span className="text-foreground-muted">
                  Оплачено{" "}
                  <span className="font-mono font-semibold tabular-nums text-foreground">
                    {totalPaid.toFixed(2)}
                  </span>
                </span>
                <span
                  className={cn(settled ? "text-success-foreground" : "text-warning-foreground")}
                >
                  Остаток{" "}
                  <span className="font-mono font-semibold tabular-nums">
                    {Math.max(0, remaining).toFixed(2)}
                  </span>
                </span>
              </div>
            ) : null}
          </div>
          <p className="shrink-0 text-right font-mono text-4xl font-bold tabular-nums text-foreground">
            {totalDue.toFixed(2)}{" "}
            <span className="font-sans text-sm font-semibold text-foreground-secondary">
              {currency}
            </span>
          </p>
        </div>
      </header>

      {isDraft ? (
        <>
          {paymentSettingsLoading ? (
            <div
              className="flex min-h-14 items-center border-b border-border px-4 text-sm text-foreground-muted"
              role="status"
            >
              Загрузка способов оплаты…
            </div>
          ) : (
            <div
              className="grid border-b border-border"
              style={{
                gridTemplateColumns: `repeat(${Math.max(1, paymentMethods.length)}, minmax(0, 1fr))`,
              }}
            >
              {paymentMethodOptions
                .filter((method) => paymentMethods.includes(method))
                .map((method) => (
                  <button
                    key={method}
                    type="button"
                    aria-pressed={selectedMethod === method}
                    onClick={() => chooseMethod(method)}
                    disabled={
                      settled ||
                      payingMethod !== null ||
                      totalDue <= 0 ||
                      completing ||
                      completionUncertain ||
                      (lockedMethod !== null && lockedMethod !== method) ||
                      (pendingPaymentMethod !== null && pendingPaymentMethod !== method)
                    }
                    className={cn(
                      "pos-tile flex min-h-14 min-w-0 flex-col items-center justify-center gap-1 border-r border-border px-1 text-xs font-semibold text-foreground transition-colors duration-fast last:border-r-0 2xl:flex-row 2xl:gap-2 2xl:px-2 2xl:text-sm",
                      touch && "min-h-16 text-base",
                      selectedMethod === method
                        ? "bg-primary text-primary-foreground"
                        : "bg-surface hover:bg-primary/5",
                      "disabled:cursor-not-allowed disabled:opacity-50",
                    )}
                  >
                    <PaymentMethodIcon method={method} active={selectedMethod === method} />
                    <span className="whitespace-nowrap">{paymentMethodLabel[method]}</span>
                  </button>
                ))}
            </div>
          )}

          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3">
            {!mixedPaymentEnabled && paymentMethods.length > 1 ? (
              <p className="mb-3 rounded-md border border-border bg-background px-3 py-2 text-xs leading-5 text-foreground-muted">
                Смешанная оплата отключена. Весь чек оплачивается одним способом.
              </p>
            ) : null}

            {pendingPaymentMethod !== null && !pendingMethodVisible ? (
              <div className="mb-3 rounded-md border border-warning/40 bg-warning-subtle p-3 text-sm text-warning-foreground">
                <p>
                  Восстанавливается ранее начатая оплата:{" "}
                  <strong>{paymentMethodLabel[pendingPaymentMethod]}</strong>.
                </p>
                {onRetryPendingPayment ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    className="mt-2"
                    onClick={onRetryPendingPayment}
                  >
                    Повторить сохранённую операцию
                  </Button>
                ) : null}
              </div>
            ) : null}

            {selectedMethod === "cash" ? (
              <>
                <label
                  htmlFor="cash-received"
                  className="mb-1.5 flex items-center justify-between gap-3 text-sm font-medium text-foreground-secondary"
                >
                  <span>Получено</span>
                  <span className="text-xs font-semibold text-foreground-muted">{currency}</span>
                </label>
                <input
                  id="cash-received"
                  type="text"
                  inputMode="decimal"
                  value={cashReceived}
                  aria-label="Получено наличными"
                  onChange={(event) => {
                    const next = event.target.value.replace(".", ",");
                    if (/^\d*(?:,\d{0,2})?$/.test(next)) setCashReceived(next);
                  }}
                  className={cn(
                    "h-12 w-full rounded-md border border-input bg-surface px-3 text-right font-mono text-2xl tabular-nums text-foreground shadow-sm focus:border-ring",
                    touch && "h-14 text-2xl",
                  )}
                />

                <div className="mt-3 grid flex-1 grid-cols-4 overflow-hidden rounded-md border border-border bg-surface">
                  {cashKeys.map((key) => (
                    <button
                      key={key}
                      type="button"
                      aria-label={cashKeyLabel(key)}
                      onClick={() => pressCashKey(key)}
                      className={cn(
                        "min-h-12 border-b border-r border-border bg-surface text-lg font-medium text-foreground transition-colors duration-fast hover:bg-primary/5 active:bg-primary/10",
                        touch && "min-h-14 text-xl",
                        (key === "+50" || key === "+100") && "text-primary",
                        key === "clear" && "text-danger",
                      )}
                    >
                      {cashKeyText(key)}
                    </button>
                  ))}
                </div>

                <div
                  className={cn(
                    "mt-3 flex min-h-14 items-center justify-between gap-3 rounded-md border px-3",
                    cashTenderInsufficient
                      ? "border-warning/40 bg-warning-subtle text-warning-foreground"
                      : "border-success/35 bg-success-subtle text-success-foreground",
                  )}
                  aria-live="polite"
                >
                  <span className="text-sm font-semibold">
                    {cashTenderInsufficient ? "Недостаточно" : "Сдача"}
                  </span>
                  <strong className="font-mono text-2xl tabular-nums">
                    {change.toFixed(2)} <span className="font-sans text-sm">{currency}</span>
                  </strong>
                </div>
              </>
            ) : (
              <div className="flex min-h-56 flex-1 flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border bg-background px-6 text-center">
                <PaymentMethodIcon method={selectedMethod} active />
                <div>
                  <p className="font-semibold text-foreground">
                    {paymentMethodLabel[selectedMethod]}
                  </p>
                  <p className="mt-1 font-mono text-2xl font-bold tabular-nums text-foreground">
                    {Math.max(0, remaining).toFixed(2)}{" "}
                    <span className="font-sans text-sm">{currency}</span>
                  </p>
                </div>
              </div>
            )}

            {payments.length > 0 ? (
              <div className="mt-3 space-y-2">
                <ul className="max-h-28 divide-y divide-border overflow-y-auto border-y border-border text-sm">
                  {payments.map((payment) => (
                    <li
                      key={payment.id}
                      className="flex items-center justify-between gap-3 px-2 py-2"
                    >
                      <span className="truncate text-foreground-secondary">
                        {paymentMethodLabel[payment.payment_method]}
                      </span>
                      <span className="shrink-0 font-mono tabular-nums text-foreground">
                        {Number(payment.amount).toFixed(2)} {payment.currency}
                      </span>
                    </li>
                  ))}
                </ul>
                {onClearPayments ? (
                  <Button type="button" size="sm" variant="ghost" onClick={onClearPayments}>
                    Очистить оплату
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>

          <footer className="border-t border-border p-3">
            <Button
              size="xl"
              variant="success"
              className="w-full"
              onClick={onComplete}
              isLoading={completing}
              disabled={!settled || pendingPaymentMethod !== null}
              title={completeHint}
            >
              <CheckIcon />
              Завершить продажу
            </Button>
          </footer>
        </>
      ) : null}

      {!isDraft && completedReceiptNumber ? (
        <div className="m-3 space-y-3 rounded-lg border border-success/40 bg-success-subtle p-4 text-center">
          <p className="font-medium text-success-foreground">
            Чек № {completedReceiptNumber} оформлен
          </p>
          {onPrint ? (
            <Button size="xl" variant="secondary" className="w-full" onClick={onPrint}>
              Печать чека
            </Button>
          ) : null}
          {onNewSale ? (
            <Button size="xl" className="w-full" onClick={onNewSale} title={newSaleHint}>
              + Новая продажа
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function parseCash(value: string): number {
  const parsed = Number(value.replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
}

function applyCashKey(current: string, key: CashKey): string {
  if (key === "clear") return "";
  if (key === "backspace") return current.slice(0, -1);
  if (key === "+50" || key === "+100") {
    return (parseCash(current) + Number(key.slice(1))).toFixed(2).replace(".", ",");
  }
  if (key === ",") {
    if (current.includes(",")) return current;
    return `${current || "0"},`;
  }

  const next = `${current}${key}`;
  const normalized = next.replace(/^0+(?=\d)/, "");
  const [, decimals = ""] = normalized.split(",");
  return decimals.length <= 2 ? normalized : current;
}

function cashKeyLabel(key: CashKey): string {
  if (key === "backspace") return "Удалить последнюю цифру";
  if (key === "clear") return "Очистить полученную сумму";
  if (key === ",") return "Десятичная запятая";
  if (key.startsWith("+")) return `Добавить ${key.slice(1)}`;
  return `Цифра ${key}`;
}

function cashKeyText(key: CashKey): string {
  if (key === "backspace") return "⌫";
  if (key === "clear") return "C";
  return key;
}

function PaymentMethodIcon({
  method,
  active,
}: {
  method: PaymentMethod;
  active: boolean;
}): JSX.Element {
  const colorClass = active ? "text-current" : "text-primary";

  if (method === "qr") {
    return (
      <svg
        aria-hidden="true"
        width="21"
        height="21"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={colorClass}
      >
        <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4z" />
        <path d="M15 14h1v1h-1zM19 14h1v3h-1zM14 18h3v2h-3zM19 19h1v1h-1z" />
      </svg>
    );
  }

  return (
    <span
      className={cn("relative block h-4 w-7 rounded-sm border-2 border-current", colorClass)}
      aria-hidden="true"
    >
      {method === "cash" ? (
        <span className="absolute left-1/2 top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-current" />
      ) : (
        <span className="absolute inset-x-0 top-1 h-0.5 bg-current" />
      )}
    </span>
  );
}

function isCurrentPaymentMethod(method: PaymentMethodRead): method is PaymentMethod {
  return method === "cash" || method === "card" || method === "qr";
}

function CheckIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="22"
      height="22"
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
