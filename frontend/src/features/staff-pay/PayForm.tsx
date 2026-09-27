import { useRef, useState } from "react";
import { useForm } from "react-hook-form";

import { Button, Checkbox, FormError, Input, Label, Modal, Select } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";
import { createOperationId } from "@/lib/operationId";

import { payDate, pharmacyToday } from "./format";
import { useStaffPayCommands } from "./queries";
import { type StaffPayPeriod } from "./types";
import { payAmount, payFormSchema, type PayFormValues } from "./validation";

export type PayIntent = { kind: "create" } | { kind: "edit" | "entry"; period: StaffPayPeriod };

type ConfirmedPayFormValues = PayFormValues & { cash_confirmed: boolean };

function moneyCents(value: string): bigint | null {
  const parsed = payAmount.safeParse(value);
  if (!parsed.success) return null;
  const [whole = "0", fraction = ""] = parsed.data.split(".");
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
}

function formatCents(value: bigint): string {
  return `${new Intl.NumberFormat("ru-RU").format(value / 100n)},${(value % 100n).toString().padStart(2, "0")} TJS`;
}

export function PayForm({
  intent,
  employeeId,
  timezone,
  onClose,
  contextUnavailable = false,
}: {
  intent: PayIntent;
  employeeId: string;
  timezone: string;
  onClose: () => void;
  contextUnavailable?: boolean;
}): JSX.Element {
  const commands = useStaffPayCommands();
  const operationId = useRef(createOperationId());
  const today = pharmacyToday(timezone);
  const period = intent.kind === "create" ? null : intent.period;
  const form = useForm<ConfirmedPayFormValues>({
    defaultValues: {
      amount: intent.kind === "edit" ? (period?.amount ?? "") : "",
      period_from: period?.period_from ?? `${today.slice(0, 7)}-01`,
      period_to: period?.period_to ?? today,
      paid_on: today,
      kind: "payment",
      note: intent.kind === "edit" ? (period?.note ?? "") : "",
      cash_confirmed: false,
    },
  });
  const [error, setError] = useState<string | null>(null);
  // Keep an uncertain cash command identical on retry. A new amount must be a new intent.
  const submitted = useRef<PayFormValues | null>(null);
  const [locked, setLocked] = useState(false);
  const pending =
    commands.create.isPending || commands.update.isPending || commands.entry.isPending;
  const awaitingContext = contextUnavailable && !locked;
  const title =
    intent.kind === "create"
      ? "Добавить период"
      : intent.kind === "edit"
        ? "Изменить сумму"
        : "Записать выдачу";
  const entryAmount = moneyCents(form.watch("amount"));
  const periodAmount = period ? moneyCents(period.amount) : null;
  // paid_amount already includes advances; do not add advance_amount again.
  const paidAmount = period ? moneyCents(period.paid_amount) : null;
  const remainingAfter =
    entryAmount !== null && entryAmount > 0n && periodAmount !== null && paidAmount !== null
      ? periodAmount - paidAmount - entryAmount
      : null;

  const submit = form.handleSubmit(async (raw) => {
    if (pending || (contextUnavailable && submitted.current === null)) return;
    form.clearErrors();
    const result = payFormSchema.safeParse(submitted.current ?? raw);
    if (!result.success) {
      for (const issue of result.error.issues) {
        form.setError(issue.path[0] as keyof PayFormValues, { message: issue.message });
      }
      return;
    }
    const values = result.data;
    if (intent.kind === "create" && values.period_to < values.period_from) {
      form.setError("period_to", { message: "Конец периода должен быть не раньше начала" });
      return;
    }
    if (intent.kind === "entry" && /^0+(?:\.0+)?$/.test(values.amount)) {
      form.setError("amount", { message: "Сумма выдачи должна быть больше нуля" });
      return;
    }
    if (intent.kind === "entry" && values.paid_on > today) {
      form.setError("paid_on", { message: "Выдачу нельзя записать будущей датой" });
      return;
    }
    submitted.current = values;
    setLocked(true);
    setError(null);
    try {
      if (intent.kind === "create")
        await commands.create.mutateAsync({
          employee_id: employeeId,
          period_from: values.period_from,
          period_to: values.period_to,
          amount: values.amount,
          note: values.note,
          operation_id: operationId.current,
        });
      else if (intent.kind === "edit")
        await commands.update.mutateAsync({
          id: intent.period.id,
          payload: {
            amount: values.amount,
            note: values.note,
            expected_row_version: intent.period.row_version,
          },
        });
      else
        await commands.entry.mutateAsync({
          id: intent.period.id,
          payload: {
            amount: values.amount,
            note: values.note,
            kind: values.kind,
            paid_on: values.paid_on,
            operation_id: operationId.current,
          },
        });
      onClose();
    } catch (cause) {
      setError(
        describeApiError(
          cause,
          "Не удалось подтвердить запись. Повторите тот же запрос или проверьте историю перед новой записью.",
        ),
      );
    }
  });
  return (
    <Modal
      open
      onClose={() => {
        if (!pending) onClose();
      }}
      title={title}
    >
      <form onSubmit={(event) => void submit(event)} className="space-y-4">
        {intent.kind === "entry" && period && !contextUnavailable ? (
          <div className="rounded-xl border border-primary/15 bg-primary/5 px-4 py-3">
            <p className="text-sm font-semibold text-foreground">Выдача за выбранный период</p>
            <p className="mt-1 text-sm text-foreground-muted">
              {payDate(period.period_from)} — {payDate(period.period_to)}
            </p>
            <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-foreground-muted">Сумма за период</dt>
                <dd className="mt-1 font-medium tabular-nums">
                  {periodAmount !== null ? formatCents(periodAmount) : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-foreground-muted">Уже выдано наличными</dt>
                <dd className="mt-1 font-medium tabular-nums">
                  {paidAmount !== null ? formatCents(paidAmount) : "—"}
                </dd>
              </div>
            </dl>
          </div>
        ) : null}
        <p className="text-sm text-foreground-muted">
          {intent.kind === "entry"
            ? "Укажите наличные, которые уже выдали сотруднику. Запись не списывает деньги из кассы."
            : "Сумму за период вносит руководитель вручную. Смены и продажи её не рассчитывают."}
        </p>
        {contextUnavailable && intent.kind !== "entry" ? (
          <p className="rounded-lg bg-primary/5 p-4 text-sm" role="status">
            {locked
              ? "Нет актуальных данных. Ранее отправленный запрос сохранён для повтора."
              : "Нет актуальных данных. Новую запись можно сохранить после восстановления связи."}
          </p>
        ) : null}
        <fieldset disabled={pending || locked || awaitingContext} className="space-y-4">
          {intent.kind === "create" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="pay-from">Начало периода</Label>
                <Input id="pay-from" type="date" {...form.register("period_from")} />
                <FormError>{form.formState.errors.period_from?.message}</FormError>
              </div>
              <div>
                <Label htmlFor="pay-to">Конец периода</Label>
                <Input id="pay-to" type="date" {...form.register("period_to")} />
                <FormError>{form.formState.errors.period_to?.message}</FormError>
              </div>
            </div>
          ) : null}
          {intent.kind === "entry" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="pay-kind">Вид выдачи</Label>
                <Select id="pay-kind" {...form.register("kind")}>
                  <option value="payment">Выплата</option>
                  <option value="advance">Аванс</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="pay-date">Дата выдачи</Label>
                <Input id="pay-date" type="date" max={today} {...form.register("paid_on")} />
                <FormError>{form.formState.errors.paid_on?.message}</FormError>
              </div>
            </div>
          ) : null}
          <div>
            <Label htmlFor="pay-amount">
              {intent.kind === "entry" ? "Сумма выдачи, TJS" : "Сумма за период, TJS"}
            </Label>
            <Input
              id="pay-amount"
              inputMode="decimal"
              autoComplete="off"
              {...form.register("amount")}
            />
            <FormError>{form.formState.errors.amount?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="pay-note">Комментарий</Label>
            <Input id="pay-note" maxLength={1000} autoComplete="off" {...form.register("note")} />
            <FormError>{form.formState.errors.note?.message}</FormError>
          </div>
          {intent.kind === "entry" ? (
            <>
              <div className="rounded-lg bg-primary/5 p-4 text-sm leading-6" aria-live="polite">
                <p className="font-medium">
                  {contextUnavailable
                    ? "Нет актуальных данных периода. Остаток после записи недоступен."
                    : remainingAfter === null
                      ? "Введите сумму выдачи, чтобы увидеть остаток после записи."
                      : remainingAfter < 0n
                        ? `После записи: выдано сверх суммы ${formatCents(-remainingAfter)}. Это не создаёт долг сотрудника.`
                        : `Остаток после записи: ${formatCents(remainingAfter)}`}
                </p>
                {!contextUnavailable ? (
                  <p className="mt-1 text-xs text-foreground-muted">
                    Предварительный расчёт по загруженным данным. Итог подтвердит сервер.
                  </p>
                ) : null}
                <p className="mt-1 text-xs text-foreground-muted">
                  Аванс уже входит в выданное и учитывается один раз.
                </p>
              </div>
              <div className="rounded-lg bg-primary/5 p-4">
                <Label htmlFor="pay-cash-confirmed" className="flex items-start gap-3">
                  <Checkbox
                    id="pay-cash-confirmed"
                    aria-invalid={!!form.formState.errors.cash_confirmed}
                    aria-describedby={
                      form.formState.errors.cash_confirmed ? "pay-cash-confirmed-error" : undefined
                    }
                    {...form.register("cash_confirmed", {
                      validate: (value) =>
                        value || "Подтвердите, что наличные уже выданы сотруднику",
                    })}
                  />
                  Наличные уже выданы сотруднику
                </Label>
                <div id="pay-cash-confirmed-error">
                  <FormError>{form.formState.errors.cash_confirmed?.message}</FormError>
                </div>
              </div>
            </>
          ) : null}
        </fieldset>
        <FormError>{error}</FormError>
        {locked && error ? (
          <p className="text-sm text-foreground-muted">
            Данные запроса сохранены до закрытия окна. При конфликте версии закройте окно и обновите
            сведения.
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" disabled={pending} onClick={onClose}>
            Закрыть
          </Button>
          <Button type="submit" disabled={pending || awaitingContext}>
            {pending ? "Сохранение…" : error ? "Повторить запрос" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
