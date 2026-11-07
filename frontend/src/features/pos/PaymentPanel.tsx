import { lazy, Suspense } from "react";

import { PosPaymentMethodButtons } from "./PosPaymentMethodButtons";
import { type PaymentEntryFormProps } from "./PaymentEntryForm";

const PaymentEntryForm = lazy(() =>
  import("./PaymentEntryForm").then((module) => ({ default: module.PaymentEntryForm })),
);

/** Empty receipts need no form runtime. Every existing financial state loads its owner immediately. */
export function PaymentPanel(props: PaymentEntryFormProps): JSX.Element {
  const hasFinancialState =
    props.totalDue !== 0 ||
    props.totalPaid !== 0 ||
    props.remaining !== 0 ||
    props.payments.length > 0 ||
    props.pendingPaymentMethod !== null ||
    props.payingMethod !== null ||
    props.completionUncertain ||
    props.completing ||
    !props.isDraft ||
    props.completedReceiptNumber !== null;
  if (hasFinancialState)
    return (
      <Suspense
        fallback={
          <section
            aria-label="Оплата"
            className="rounded-[10px] border border-border bg-surface p-4"
          >
            <p role="status">Загружаем оплату и проверку сохранённого результата…</p>
          </section>
        }
      >
        <PaymentEntryForm {...props} />
      </Suspense>
    );

  const labels = { cash: "Наличные", card: "Карта", qr: "QR", mixed: "Смешанная" };
  const options = (["cash", "card", "qr", "mixed"] as const)
    .filter((method) =>
      method === "mixed"
        ? props.mixedPaymentEnabled && props.paymentMethods.length > 1
        : props.paymentMethods.includes(method),
    )
    .map((value) => ({ value, label: labels[value] }));
  const selected = props.paymentMethods.includes("cash")
    ? "cash"
    : (props.paymentMethods[0] ?? null);
  const settingsReady = !props.paymentSettingsLoading && !props.paymentSettingsUnavailable;
  return (
    <section
      aria-label="Оплата"
      className="space-y-3 rounded-[10px] border border-border bg-surface p-4"
    >
      <h2 className="text-base font-semibold">Оплата</h2>
      <div>
        <p className="text-[32px] font-semibold leading-tight tracking-tight tabular-nums">0,00</p>
        <p className="mt-1 text-xs text-foreground-muted">
          {props.currency === "TJS" ? "сомони" : props.currency} к оплате
        </p>
      </div>
      {settingsReady ? (
        <PosPaymentMethodButtons
          options={options}
          value={selected}
          onChange={() => undefined}
          disabled
        />
      ) : (
        <p
          role={props.paymentSettingsUnavailable ? "alert" : "status"}
          className="text-sm text-foreground-muted"
        >
          {props.paymentSettingsUnavailable
            ? "Способы оплаты временно недоступны"
            : "Загрузка способов оплаты…"}
        </p>
      )}
      {settingsReady && props.paymentMethods.includes("cash") && (
        <>
          <label htmlFor="empty-cash-received" className="block text-sm font-medium">
            Получено наличными, {props.currency}
          </label>
          <div className="flex gap-2">
            <input
              id="empty-cash-received"
              aria-label="Получено наличными"
              disabled
              readOnly
              value=""
              placeholder="0,00"
              className="h-11 min-w-0 flex-1 rounded-lg border border-input bg-surface px-3 text-right text-xl"
            />
            <button type="button" disabled className="rounded-lg border border-border px-3">
              123
            </button>
          </div>
          <div className="grid grid-cols-[1.5fr_1fr_1fr] gap-1">
            {["Без сдачи", "10", "50"].map((label) => (
              <button
                type="button"
                key={label}
                disabled
                className="h-9 rounded-lg border border-border text-sm text-foreground-muted"
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex min-h-12 items-center justify-between gap-3 rounded-lg bg-primary/5 px-3">
            <span className="text-sm font-semibold">Сдача</span>
            <strong className="text-xl tabular-nums">—</strong>
          </div>
        </>
      )}
      <p className="text-xs text-foreground-muted">Добавьте товар в чек.</p>
      <button
        type="button"
        disabled
        className="h-11 w-full rounded-lg bg-primary/40 text-sm font-semibold text-primary-foreground"
      >
        Завершить продажу
      </button>
    </section>
  );
}
