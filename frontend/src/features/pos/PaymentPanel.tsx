import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import { paymentMethodLabel, paymentMethodOptions } from "./labels";
import {
  type Payment,
  type PaymentMetadata,
  type PaymentMethod,
  type PaymentMethodRead,
} from "./types";

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
type PaymentDisplay = Payment & { metadata?: PaymentMetadata };

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
  paymentSettingsUnavailable,
  interactionBlocked,
  completionBlocked,
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
  payments: PaymentDisplay[];
  isDraft: boolean;
  completing: boolean;
  completionUncertain: boolean;
  payingMethod: PaymentMethodRead | null;
  pendingPaymentMethod: PaymentMethodRead | null;
  paymentMethods: PaymentMethod[];
  mixedPaymentEnabled: boolean;
  paymentSettingsLoading: boolean;
  paymentSettingsUnavailable: boolean;
  interactionBlocked: boolean;
  completionBlocked: boolean;
  onPayTile: (method: PaymentMethod, amount?: string, metadata?: PaymentMetadata) => void;
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
  const [cashInputPristine, setCashInputPristine] = useState(true);
  const paymentMethodsKey = paymentMethods.join("|");
  const paymentSettingsBlocked =
    paymentSettingsLoading || paymentSettingsUnavailable || paymentMethods.length === 0;
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
    paymentMethods.includes(activeMethod) &&
    (lockedMethod === null || activeMethod === lockedMethod)
      ? activeMethod
      : fallbackMethod;
  const settled = Math.abs(remaining) <= 0.001;
  const overpaid = remaining < -0.001;
  const cashReceivedNumber = parseCash(cashReceived);
  const nonCashPaid = payments.reduce(
    (sum, payment) => (payment.payment_method === "cash" ? sum : sum + Number(payment.amount)),
    0,
  );
  const cashPaid = payments.reduce(
    (sum, payment) => (payment.payment_method === "cash" ? sum + Number(payment.amount) : sum),
    0,
  );
  const cashTendered = payments.reduce((sum, payment) => sum + cashTenderedFor(payment), 0);
  const cashDue = Math.max(0, totalDue - nonCashPaid);
  const change = Math.max(0, cashReceivedNumber - cashDue);
  const availableCash = Math.max(0, cashReceivedNumber - cashPaid);
  const cashTenderInsufficient =
    isDraft && selectedMethod === "cash" && remaining > 0.001 && availableCash + 0.001 < remaining;
  const pendingMethodVisible =
    pendingPaymentMethod !== null &&
    isCurrentPaymentMethod(pendingPaymentMethod) &&
    paymentMethods.includes(pendingPaymentMethod);
  const paymentActionDisabled =
    settled ||
    overpaid ||
    payingMethod !== null ||
    totalDue <= 0 ||
    paymentSettingsBlocked ||
    interactionBlocked ||
    completing ||
    completionUncertain ||
    pendingPaymentMethod !== null ||
    (selectedMethod === "cash" &&
      (availableCash <= 0.001 || (!mixedPaymentEnabled && cashTenderInsufficient)));

  useEffect(() => {
    setActiveMethod((current) =>
      paymentMethods.includes(current) && (lockedMethod === null || current === lockedMethod)
        ? current
        : fallbackMethod,
    );
  }, [fallbackMethod, lockedMethod, paymentMethods, paymentMethodsKey]);

  useEffect(() => {
    if (!isDraft || totalDue <= 0) return;
    if (payments.length === 0) {
      setCashReceived(formatCashInput(totalDue));
      setCashInputPristine(true);
      return;
    }
    if (cashTendered > 0) {
      setCashReceived(formatCashInput(cashTendered));
      setCashInputPristine(true);
      return;
    }
    setCashReceived(formatCashInput(cashDue));
    setCashInputPristine(true);
  }, [cashDue, cashTendered, isDraft, payments.length, totalDue]);

  const chooseMethod = (method: PaymentMethod) => {
    setActiveMethod(method);
  };

  const submitSelectedPayment = () => {
    if (paymentSettingsBlocked || paymentActionDisabled) return;
    const method = selectedMethod;
    const tendered = availableCash;
    const amount = method === "cash" ? Math.min(tendered, remaining) : Math.max(0, remaining);
    if (!mixedPaymentEnabled && method === "cash" && amount + 0.001 < remaining) return;
    if (amount <= 0) return;
    if (method === "cash") {
      onPayTile(method, amount.toFixed(2), { cash_received: tendered.toFixed(2) });
      return;
    }
    onPayTile(method);
  };

  const pressCashKey = (key: CashKey) => {
    setCashReceived((current) =>
      applyCashKey(cashInputPristine && isDigitKey(key) ? "" : current, key),
    );
    setCashInputPristine(false);
  };

  return (
    <section
      aria-labelledby="payment-panel-title"
      className="flex min-h-[26rem] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-surface sm:min-h-[30rem] xl:h-full xl:min-h-0"
    >
      <header className={cn("border-b border-border px-3 py-2", touch && "px-4 py-3")}>
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
                  {overpaid ? "Переплата" : "Остаток"}{" "}
                  <span className="font-mono font-semibold tabular-nums">
                    {Math.abs(remaining).toFixed(2)}
                  </span>
                </span>
              </div>
            ) : null}
          </div>
          <p
            className={cn(
              "shrink-0 text-right font-mono text-3xl font-bold tabular-nums text-foreground",
              touch && "text-4xl",
            )}
          >
            {totalDue.toFixed(2)}{" "}
            <span className="font-sans text-sm font-semibold text-foreground-secondary">
              {currency}
            </span>
          </p>
        </div>
      </header>

      {isDraft ? (
        <>
          {paymentSettingsBlocked ? (
            <div
              className={cn(
                "flex min-h-14 items-center border-b border-border px-4 text-sm",
                paymentSettingsUnavailable
                  ? "bg-warning-subtle text-warning-foreground"
                  : "text-foreground-muted",
              )}
              role={paymentSettingsUnavailable ? "alert" : "status"}
            >
              {paymentSettingsUnavailable
                ? "Способы оплаты временно недоступны"
                : "Загрузка способов оплаты…"}
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
                      interactionBlocked ||
                      completing ||
                      completionUncertain ||
                      (lockedMethod !== null && lockedMethod !== method) ||
                      (pendingPaymentMethod !== null && pendingPaymentMethod !== method)
                    }
                    className={cn(
                      "pos-tile flex min-h-12 min-w-0 flex-col items-center justify-center gap-1 border-r border-border px-1 text-xs font-semibold text-foreground transition-colors duration-fast last:border-r-0 2xl:flex-row 2xl:gap-2 2xl:px-2 2xl:text-sm",
                      touch && "min-h-14 text-base",
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

          <div className={cn("flex min-h-0 flex-1 flex-col overflow-y-auto p-2", touch && "p-3")}>
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

            {paymentSettingsBlocked ? (
              <div className="flex min-h-56 flex-1 items-center justify-center rounded-md border border-dashed border-border bg-background px-6 text-center">
                <p className="max-w-xs text-sm leading-6 text-foreground-muted">
                  {paymentSettingsUnavailable
                    ? "Новые платежи заблокированы до восстановления связи с сервером настроек."
                    : "Ожидаем подтверждённые настройки аптеки перед приёмом оплаты."}
                </p>
              </div>
            ) : selectedMethod === "cash" ? (
              <>
                <label
                  htmlFor="cash-received"
                  className="mb-1 flex items-center justify-between gap-3 text-sm font-medium text-foreground-secondary"
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
                  disabled={interactionBlocked || completing || completionUncertain}
                  onChange={(event) => {
                    const next = event.target.value.replace(".", ",");
                    if (/^\d{0,12}(?:,\d{0,2})?$/.test(next)) {
                      setCashReceived(next);
                      setCashInputPristine(false);
                    }
                  }}
                  className={cn(
                    "h-10 w-full rounded-md border border-input bg-surface px-3 text-right font-mono text-xl tabular-nums text-foreground shadow-sm focus:border-ring",
                    touch && "h-12 text-2xl",
                  )}
                />

                <div className="mt-2 grid flex-1 grid-cols-4 overflow-hidden rounded-md border border-border bg-surface">
                  {cashKeys.map((key) => (
                    <button
                      key={key}
                      type="button"
                      aria-label={cashKeyLabel(key)}
                      onClick={() => pressCashKey(key)}
                      disabled={interactionBlocked || completing || completionUncertain}
                      className={cn(
                        "min-h-9 border-b border-r border-border bg-surface text-base font-medium text-foreground transition-colors duration-fast hover:bg-primary/5 active:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50",
                        touch && "min-h-11 text-xl",
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
                    "mt-2 flex min-h-11 items-center justify-between gap-3 rounded-md border px-3",
                    touch && "min-h-12",
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
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={onClearPayments}
                    title="Удалить только расчёт оплаты до завершения продажи. Товары и операции внешнего терминала не изменятся."
                  >
                    Сбросить расчёт
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>

          <footer className={cn("space-y-1 border-t border-border p-2", touch && "space-y-2 p-3")}>
            {!paymentSettingsBlocked && !settled && !overpaid ? (
              <Button
                size="xl"
                className={cn("h-11 w-full", touch && "h-12")}
                onClick={submitSelectedPayment}
                disabled={paymentActionDisabled}
              >
                <PaymentMethodIcon method={selectedMethod} active />
                {paymentActionLabel(selectedMethod)}
              </Button>
            ) : null}
            <Button
              size="xl"
              variant="success"
              className={cn("h-11 w-full", touch && "h-12")}
              onClick={onComplete}
              isLoading={completing}
              disabled={
                !settled ||
                overpaid ||
                pendingPaymentMethod !== null ||
                interactionBlocked ||
                completionBlocked
              }
              title={settled ? completeHint : "Сначала подтвердите полную оплату"}
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

function formatCashInput(value: number): string {
  return value.toFixed(2).replace(".", ",");
}

function cashTenderedFor(payment: PaymentDisplay): number {
  if (payment.payment_method !== "cash") return 0;
  const allocated = Number(payment.amount);
  const received = Number(payment.metadata?.cash_received);
  return Number.isFinite(received) && received >= allocated ? received : allocated;
}

function isDigitKey(key: CashKey): boolean {
  return /^\d+$/.test(key);
}

function applyCashKey(current: string, key: CashKey): string {
  if (key === "clear") return "";
  if (key === "backspace") return current.slice(0, -1);
  if (key === "+50" || key === "+100") {
    const next = parseCash(current) + Number(key.slice(1));
    return next <= 999_999_999_999.99 ? formatCashInput(next) : current;
  }
  if (key === ",") {
    if (current.includes(",")) return current;
    return `${current || "0"},`;
  }

  const next = `${current}${key}`;
  const normalized = next.replace(/^0+(?=\d)/, "");
  return /^\d{0,12}(?:,\d{0,2})?$/.test(normalized) ? normalized : current;
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

function paymentActionLabel(method: PaymentMethod): string {
  if (method === "cash") return "Принять наличные";
  if (method === "card") return "Перейти к оплате картой";
  return "Перейти к оплате по QR";
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
