import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import * as z from "zod/mini";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { paymentMethodLabel, paymentMethodOptions } from "./labels";
import { PosPaymentMethodButtons } from "./PosPaymentMethodButtons";
import {
  type Payment,
  type PaymentMetadata,
  type PaymentMethod,
  type PaymentMethodRead,
} from "./types";

const NumPad = lazy(() => import("./NumPad"));
const moneyInput = z.string().check(z.trim(), z.regex(/^\d{1,12}(?:[.,]\d{1,2})?$/));
const paymentFormSchema = z.object({
  method: z.enum(["cash", "card", "qr", "mixed"]),
  received: z.string(),
  firstMethod: z.enum(["cash", "card", "qr"]),
  secondMethod: z.enum(["cash", "card", "qr"]),
  firstAmount: z.string(),
  firstBaseline: z.number().check(z.gte(0)),
});
type PaymentForm = z.infer<typeof paymentFormSchema>;
type Choice = PaymentForm["method"];
type PaymentDisplay = Payment & { metadata?: PaymentMetadata };
const choiceLabel = { cash: "Наличные", card: "Карта", qr: "QR", mixed: "Смешанная" };

/** SaleArea owns commands and recovery; this form requests only the next explicit step. */
export function PaymentEntryForm({
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
  onPayTile: (method: PaymentMethod, amount?: string, metadata?: PaymentMetadata) => boolean | void;
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
  const initialMethod = paymentMethods.includes("cash") ? "cash" : (paymentMethods[0] ?? "cash");
  const form = useForm<PaymentForm>({
    defaultValues: {
      method: initialMethod,
      received: formatCashInput(totalDue),
      firstMethod: initialMethod,
      secondMethod: paymentMethods.find((method) => method !== initialMethod) ?? "card",
      firstAmount: "",
      firstBaseline: 0,
    },
  });
  const values = form.watch();
  const acceptedCashSnapshot = useRef<string | null>(null);
  const requestedClearSnapshot = useRef<string | null>(null);
  const paymentSnapshot = JSON.stringify([remaining, payments]);
  const [keypad, setKeypad] = useState<"received" | "firstAmount" | null>(null);
  const paymentSettingsBlocked =
    paymentSettingsLoading || paymentSettingsUnavailable || paymentMethods.length === 0;
  const settled = Math.abs(remaining) <= 0.001;
  const overpaid = remaining < -0.001;
  const recordedMethod = payments[0]?.payment_method;
  const lockedMethod =
    !mixedPaymentEnabled &&
    recordedMethod !== undefined &&
    isCurrentPaymentMethod(recordedMethod) &&
    paymentMethods.includes(recordedMethod)
      ? recordedMethod
      : null;
  const mixedAvailable = mixedPaymentEnabled && paymentMethods.length > 1;
  const selectedMethod: Choice =
    values.method === "mixed" && mixedAvailable
      ? "mixed"
      : values.method !== "mixed" &&
          paymentMethods.includes(values.method) &&
          (lockedMethod === null || lockedMethod === values.method)
        ? values.method
        : (lockedMethod ?? initialMethod);
  const mixed = selectedMethod === "mixed";
  const paidFor = (method: PaymentMethod) =>
    payments.reduce(
      (sum, payment) => (payment.payment_method === method ? sum + Number(payment.amount) : sum),
      0,
    );
  const firstAmount = parseCash(values.firstAmount);
  const firstDelta = paidFor(values.firstMethod) - values.firstBaseline;
  const firstRecorded = mixed && firstAmount > 0 && Math.abs(firstDelta - firstAmount) <= 0.001;
  const firstChanged = mixed && Math.abs(firstDelta) > 0.001 && !firstRecorded;
  const splitLocked = firstRecorded || firstChanged;
  const mixedMethodsValid =
    values.firstMethod !== values.secondMethod &&
    paymentMethods.includes(values.firstMethod) &&
    paymentMethods.includes(values.secondMethod);
  const mixedAmountValid =
    moneyInput.safeParse(values.firstAmount).success &&
    firstAmount > 0 &&
    (firstRecorded || firstAmount + 0.001 < remaining);
  const actionMethod = mixed
    ? firstRecorded
      ? values.secondMethod
      : values.firstMethod
    : selectedMethod;
  const plannedAmount = mixed && !firstRecorded ? firstAmount : Math.max(0, remaining);
  const cashPaid = paidFor("cash");
  const nonCashPaid = totalPaid - cashPaid;
  const cashDue = Math.max(0, totalDue - nonCashPaid);
  const cashTendered = payments.reduce((sum, payment) => sum + cashTenderedFor(payment), 0);
  const cashReceived = parseCash(values.received);
  const availableCash = Math.max(0, cashReceived - cashPaid);
  const actionAmount =
    actionMethod === "cash" && !mixed && mixedPaymentEnabled
      ? Math.min(availableCash, plannedAmount)
      : plannedAmount;
  const cashTarget = mixed
    ? values.firstMethod === "cash"
      ? firstRecorded
        ? cashPaid
        : cashPaid + firstAmount
      : values.secondMethod === "cash"
        ? cashPaid + (firstRecorded ? remaining : remaining - firstAmount)
        : 0
    : cashDue;
  const change = Math.max(0, cashReceived - cashTarget);
  const showCash =
    selectedMethod === "cash" ||
    (mixed && (values.firstMethod === "cash" || values.secondMethod === "cash"));
  const cashInsufficient =
    actionMethod === "cash" &&
    (!moneyInput.safeParse(values.received).success || availableCash + 0.001 < plannedAmount);
  const cashActionInvalid =
    actionMethod === "cash" &&
    (!moneyInput.safeParse(values.received).success ||
      availableCash <= 0.001 ||
      ((mixed || !mixedPaymentEnabled) && cashInsufficient));
  const busy =
    interactionBlocked ||
    completing ||
    completionUncertain ||
    payingMethod !== null ||
    pendingPaymentMethod !== null;
  const paymentActionDisabled =
    settled ||
    overpaid ||
    totalDue <= 0 ||
    paymentSettingsBlocked ||
    busy ||
    cashActionInvalid ||
    (mixed && (!mixedMethodsValid || !mixedAmountValid || firstChanged));
  const completionGuidance = getCompletionGuidance({
    remaining,
    currency,
    settled,
    overpaid,
    pendingPaymentMethod,
    interactionBlocked,
    completionBlocked,
  });

  useEffect(() => {
    if (!isDraft || totalDue <= 0) return;
    // A confirmed first part changes totals, but must not replace cash already entered for the second.
    if (form.getFieldState("received").isDirty) return;
    form.setValue("received", formatCashInput(cashTendered > 0 ? cashTendered : cashDue));
  }, [cashDue, cashTendered, isDraft, payments.length, totalDue, form]);

  useEffect(() => {
    if (
      requestedClearSnapshot.current === null ||
      requestedClearSnapshot.current === paymentSnapshot ||
      payments.length !== 0
    )
      return;
    requestedClearSnapshot.current = null;
    acceptedCashSnapshot.current = null;
    form.setValue("firstBaseline", 0);
    form.setValue("firstAmount", "");
  }, [paymentSnapshot, payments.length, form]);

  const chooseMethod = (method: Choice) => {
    if (busy || settled || splitLocked || paymentSettingsBlocked || totalDue <= 0) return;
    if (method === "mixed") {
      if (!mixedAvailable) return;
      const first = paymentMethods.includes(values.firstMethod)
        ? values.firstMethod
        : initialMethod;
      const second =
        paymentMethods.includes(values.secondMethod) && values.secondMethod !== first
          ? values.secondMethod
          : paymentMethods.find((candidate) => candidate !== first);
      if (!second) return;
      form.setValue("firstMethod", first);
      form.setValue("secondMethod", second);
      if (values.method !== "mixed") form.setValue("firstBaseline", paidFor(first));
    } else if (
      !paymentMethods.includes(method) ||
      (lockedMethod !== null && lockedMethod !== method)
    )
      return;
    form.setValue("method", method);
  };
  const choosePartMethod = (part: "firstMethod" | "secondMethod", method: PaymentMethod) => {
    if (busy || firstRecorded || firstChanged || !paymentMethods.includes(method)) return;
    const other = part === "firstMethod" ? "secondMethod" : "firstMethod";
    const alternative = paymentMethods.find((candidate) => candidate !== method);
    if (!alternative) return;
    form.setValue(part, method);
    if (values[other] === method) form.setValue(other, alternative);
    form.setValue(
      "firstBaseline",
      paidFor(part === "firstMethod" ? method : form.getValues("firstMethod")),
    );
  };
  const submitSelectedPayment = () => {
    if (
      paymentActionDisabled ||
      !paymentFormSchema.safeParse(form.getValues()).success ||
      actionAmount <= 0
    )
      return;
    // Only owner props can confirm the first part. This component never auto-starts the second.
    if (actionMethod === "cash") {
      if (acceptedCashSnapshot.current === paymentSnapshot) return;
      acceptedCashSnapshot.current = paymentSnapshot;
      try {
        const accepted = onPayTile("cash", actionAmount.toFixed(2), {
          cash_received: availableCash.toFixed(2),
        });
        // Async legacy owners retain their guards; only synchronous staging acknowledges here.
        if (accepted !== true) acceptedCashSnapshot.current = null;
      } catch (error) {
        acceptedCashSnapshot.current = null;
        throw error;
      }
    } else onPayTile(actionMethod, actionAmount.toFixed(2));
  };
  const enterMoney = (field: "received" | "firstAmount", value: string) => {
    const next = value.replace(".", ",");
    if (/^\d{0,12}(?:,\d{0,2})?$/.test(next)) form.setValue(field, next, { shouldDirty: true });
  };
  const methodOptions = paymentMethodOptions
    .filter((method) => paymentMethods.includes(method))
    .map((method) => ({ value: method, label: choiceLabel[method] }));
  const choices: { value: Choice; label: string; disabled?: boolean }[] = methodOptions.map(
    (option) => ({
      ...option,
      disabled: lockedMethod !== null && lockedMethod !== option.value,
    }),
  );
  if (mixedAvailable) choices.push({ value: "mixed", label: choiceLabel.mixed });

  return (
    <section
      aria-labelledby="payment-panel-title"
      className="flex min-w-0 flex-col overflow-hidden rounded-[10px] border border-border bg-surface xl:h-full xl:min-h-0"
    >
      <header className="shrink-0 px-4 pb-2 pt-4">
        <h2 id="payment-panel-title" className="text-base font-semibold">
          Оплата
        </h2>
        <p className="mt-2 text-[32px] font-semibold leading-tight tracking-tight tabular-nums">
          {formatCashInput(totalDue)}
        </p>
        <p className="mt-1 text-xs text-foreground-muted">
          {currency === "TJS" ? "сомони" : currency} к оплате
        </p>
        {totalDue > 0 && (
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
            <span className="text-foreground-muted">
              Оплачено <strong>{formatCashInput(totalPaid)}</strong>
            </span>
            <span className={settled ? "text-success-foreground" : "text-warning-foreground"}>
              {overpaid ? "Переплата" : "Остаток"}{" "}
              <strong>{formatCashInput(Math.abs(remaining))}</strong>
            </span>
          </div>
        )}
      </header>
      {isDraft && (
        <>
          {paymentSettingsBlocked ? (
            <div
              className="px-4 py-3 text-sm"
              role={paymentSettingsUnavailable ? "alert" : "status"}
            >
              {paymentSettingsUnavailable
                ? "Способы оплаты временно недоступны"
                : "Загрузка способов оплаты…"}
            </div>
          ) : (
            <div className="shrink-0 px-4 py-3">
              <PosPaymentMethodButtons
                options={choices}
                value={selectedMethod}
                onChange={chooseMethod}
                disabled={busy || settled || splitLocked || totalDue <= 0}
              />
            </div>
          )}
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-4">
            {pendingPaymentMethod !== null && (
              <p
                className="rounded-lg bg-warning-subtle p-3 text-sm text-warning-foreground"
                role="status"
              >
                Восстанавливается ранее начатая оплата:{" "}
                <strong>{paymentMethodLabel[pendingPaymentMethod]}</strong>.
              </p>
            )}
            {paymentSettingsBlocked ? (
              <p className="text-sm text-foreground-muted">
                {paymentSettingsUnavailable
                  ? "Новые платежи заблокированы до восстановления связи с сервером настроек."
                  : "Ожидаем подтверждённые настройки аптеки перед приёмом оплаты."}
              </p>
            ) : (
              <>
                {!mixedPaymentEnabled && paymentMethods.length > 1 && (
                  <p className="text-xs text-foreground-muted">
                    Смешанная оплата отключена. Весь чек оплачивается одним способом.
                  </p>
                )}
                {mixed && (
                  <div className="space-y-3">
                    <PosPaymentMethodButtons
                      label="Первая часть оплаты"
                      options={methodOptions}
                      value={values.firstMethod}
                      onChange={(method) => choosePartMethod("firstMethod", method)}
                      disabled={busy || firstRecorded || firstChanged}
                    />
                    <label htmlFor="payment-first-amount" className="block text-sm font-medium">
                      Сумма первой части, {currency}
                    </label>
                    <div className="flex gap-2">
                      <input
                        id="payment-first-amount"
                        inputMode="decimal"
                        value={values.firstAmount}
                        onChange={(event) => enterMoney("firstAmount", event.target.value)}
                        disabled={busy || firstRecorded || firstChanged}
                        className="h-11 min-w-0 flex-1 rounded-lg border border-input bg-surface px-3 text-right text-xl tabular-nums"
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        aria-label="Ввести первую часть на экранной клавиатуре"
                        disabled={busy || firstRecorded || firstChanged}
                        onClick={() => setKeypad("firstAmount")}
                      >
                        123
                      </Button>
                    </div>
                    <PosPaymentMethodButtons
                      label="Остаток оплаты"
                      options={methodOptions.filter(
                        (option) => option.value !== values.firstMethod,
                      )}
                      value={values.secondMethod}
                      onChange={(method) => choosePartMethod("secondMethod", method)}
                      disabled={busy || firstRecorded || firstChanged}
                    />
                    <p className="text-sm text-foreground-muted">
                      {firstRecorded
                        ? `Первая часть принята. Остаток ${formatCashInput(remaining)} ${currency}.`
                        : firstChanged
                          ? "Сумма первой части изменилась. Проверьте введённые платежи."
                          : mixedAmountValid
                            ? `Вторая часть: ${formatCashInput(remaining - firstAmount)} ${currency}.`
                            : "Укажите первую часть меньше оставшейся суммы чека."}
                    </p>
                  </div>
                )}
                {showCash && (
                  <>
                    <label htmlFor="cash-received" className="text-sm font-medium">
                      Получено наличными, {currency}
                    </label>
                    <div className="flex gap-2">
                      <input
                        id="cash-received"
                        inputMode="decimal"
                        aria-label="Получено наличными"
                        value={values.received}
                        onChange={(event) => enterMoney("received", event.target.value)}
                        disabled={busy}
                        className={cn(
                          "h-11 min-w-0 flex-1 rounded-lg border border-input bg-surface px-3 text-right text-xl tabular-nums",
                          touch && "h-12",
                        )}
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        aria-label="Ввести полученную сумму на экранной клавиатуре"
                        disabled={busy}
                        onClick={() => setKeypad("received")}
                      >
                        123
                      </Button>
                    </div>
                    <div className="grid grid-cols-[1.5fr_1fr_1fr] gap-1">
                      {[null, 10, 50].map((amount) => (
                        <Button
                          key={amount ?? "exact"}
                          type="button"
                          size="sm"
                          variant="secondary"
                          disabled={busy}
                          onClick={() =>
                            enterMoney(
                              "received",
                              formatCashInput(amount ?? Math.max(0, cashTarget)),
                            )
                          }
                        >
                          {amount ?? "Без сдачи"}
                        </Button>
                      ))}
                    </div>
                    <div
                      className={cn(
                        "flex min-h-12 items-center justify-between gap-3 rounded-lg px-3",
                        cashInsufficient
                          ? "bg-warning-subtle text-warning-foreground"
                          : "bg-primary/5",
                      )}
                      aria-live="polite"
                    >
                      <span className="text-sm font-semibold">
                        {cashInsufficient ? "Недостаточно" : "Сдача"}
                      </span>
                      <strong className="text-xl tabular-nums">
                        {formatCashInput(change)} <span className="text-sm">{currency}</span>
                      </strong>
                    </div>
                  </>
                )}
                {!showCash && !mixed && (
                  <p className="rounded-lg bg-primary/5 p-3 text-sm">
                    {paymentMethodLabel[actionMethod]}: {formatCashInput(Math.max(0, remaining))}{" "}
                    {currency}. Подтвердите оплату по чеку терминала.
                  </p>
                )}
              </>
            )}
            {payments.length > 0 && (
              <div className="space-y-2">
                <ul className="max-h-28 divide-y divide-border overflow-y-auto border-y border-border text-sm">
                  {payments.map((payment) => (
                    <li key={payment.id} className="flex justify-between gap-3 px-2 py-2">
                      <span>{paymentMethodLabel[payment.payment_method]}</span>
                      <span className="tabular-nums">
                        {formatCashInput(Number(payment.amount))} {payment.currency}
                      </span>
                    </li>
                  ))}
                </ul>
                {onClearPayments && (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        requestedClearSnapshot.current = paymentSnapshot;
                        onClearPayments();
                      }}
                      title="Товары останутся в чеке. Операция банковского терминала или QR-сервиса не отменяется."
                    >
                      Удалить введённую оплату
                    </Button>
                    <p className="text-xs text-foreground-muted">
                      Товары останутся в чеке; операция внешнего терминала не отменяется.
                    </p>
                  </>
                )}
              </div>
            )}
          </div>
          <footer className="shrink-0 space-y-2 px-4 pb-4">
            {completionGuidance && (
              <p
                id="completion-guidance"
                className="text-xs text-foreground-muted"
                aria-live="polite"
              >
                {completionGuidance}
              </p>
            )}
            {pendingPaymentMethod !== null && onRetryPendingPayment ? (
              <Button
                className="h-11 w-full"
                onClick={onRetryPendingPayment}
                disabled={completing || payingMethod !== null}
              >
                Проверить сохранённую оплату
              </Button>
            ) : settled || overpaid ? (
              <Button
                size="xl"
                variant="success"
                className="h-11 w-full"
                onClick={onComplete}
                isLoading={completing}
                disabled={
                  !settled ||
                  overpaid ||
                  pendingPaymentMethod !== null ||
                  interactionBlocked ||
                  completionBlocked
                }
                title={completionGuidance ?? completeHint}
                aria-describedby={completionGuidance ? "completion-guidance" : undefined}
              >
                Завершить продажу
              </Button>
            ) : (
              !paymentSettingsBlocked && (
                <Button
                  size="xl"
                  className="h-11 w-full"
                  onClick={submitSelectedPayment}
                  disabled={paymentActionDisabled}
                >
                  {paymentActionLabel(actionMethod)}
                </Button>
              )
            )}
          </footer>
        </>
      )}
      {!isDraft && completedReceiptNumber && (
        <div className="m-3 space-y-3 rounded-lg border border-success/40 bg-success-subtle p-4 text-center">
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
      {keypad && (
        <Suspense fallback={<p role="status">Открываем клавиатуру…</p>}>
          <NumPad
            title={keypad === "received" ? "Получено наличными" : "Сумма первой части"}
            initial={values[keypad].replace(",", ".")}
            onClose={() => setKeypad(null)}
            onSubmit={(value) => {
              if (!busy && !(keypad === "firstAmount" && (firstRecorded || firstChanged)))
                enterMoney(keypad, value);
              setKeypad(null);
            }}
          />
        </Suspense>
      )}
    </section>
  );
}

export type PaymentEntryFormProps = Parameters<typeof PaymentEntryForm>[0];

function getCompletionGuidance({
  remaining,
  currency,
  settled,
  overpaid,
  pendingPaymentMethod,
  interactionBlocked,
  completionBlocked,
}: {
  remaining: number;
  currency: string;
  settled: boolean;
  overpaid: boolean;
  pendingPaymentMethod: PaymentMethodRead | null;
  interactionBlocked: boolean;
  completionBlocked: boolean;
}): string | null {
  if (pendingPaymentMethod !== null) return "Сначала проверьте ранее начатую оплату.";
  if (overpaid) return "Уберите переплату, затем завершите продажу.";
  if (!settled)
    return `Сначала примите оставшиеся ${formatCashInput(Math.max(0, remaining))} ${currency}.`;
  if (interactionBlocked || completionBlocked)
    return "Дождитесь завершения текущей операции оплаты.";
  return null;
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
  const allocated = Number(payment.amount),
    received = Number(payment.metadata?.cash_received);
  return Number.isFinite(received) && received >= allocated ? received : allocated;
}
function isCurrentPaymentMethod(method: PaymentMethodRead): method is PaymentMethod {
  return method === "cash" || method === "card" || method === "qr";
}
function paymentActionLabel(method: PaymentMethod): string {
  if (method === "cash") return "Принять наличные";
  if (method === "card") return "Перейти к оплате картой";
  return "Перейти к оплате по QR";
}
