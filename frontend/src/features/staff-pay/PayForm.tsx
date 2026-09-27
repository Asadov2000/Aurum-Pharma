import { useRef, useState } from "react";
import { useForm } from "react-hook-form";

import { Button, FormError, Input, Label, Modal, Select } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";
import { createOperationId } from "@/lib/operationId";

import { pharmacyToday } from "./format";
import { useStaffPayCommands } from "./queries";
import { type StaffPayPeriod } from "./types";
import { payFormSchema, type PayFormValues } from "./validation";

export type PayIntent = { kind: "create" } | { kind: "edit" | "entry"; period: StaffPayPeriod };

export function PayForm({
  intent,
  employeeId,
  timezone,
  onClose,
}: {
  intent: PayIntent;
  employeeId: string;
  timezone: string;
  onClose: () => void;
}): JSX.Element {
  const commands = useStaffPayCommands();
  const operationId = useRef(createOperationId());
  const today = pharmacyToday(timezone);
  const period = intent.kind === "create" ? null : intent.period;
  const form = useForm<PayFormValues>({
    defaultValues: {
      amount: intent.kind === "edit" ? (period?.amount ?? "") : "",
      period_from: period?.period_from ?? `${today.slice(0, 7)}-01`,
      period_to: period?.period_to ?? today,
      paid_on: today,
      kind: "payment",
      note: intent.kind === "edit" ? (period?.note ?? "") : "",
    },
  });
  const [error, setError] = useState<string | null>(null);
  // Keep an uncertain cash command identical on retry. A new amount must be a new intent.
  const submitted = useRef<PayFormValues | null>(null);
  const [locked, setLocked] = useState(false);
  const pending =
    commands.create.isPending || commands.update.isPending || commands.entry.isPending;
  const title =
    intent.kind === "create"
      ? "Добавить период"
      : intent.kind === "edit"
        ? "Изменить сумму"
        : "Записать выдачу";

  const submit = form.handleSubmit(async (raw) => {
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
        <p className="text-sm text-foreground-muted">
          {intent.kind === "entry"
            ? "Укажите наличные, которые уже выдали сотруднику. Запись не списывает деньги из кассы."
            : "Сумму за период вносит руководитель вручную. Смены и продажи её не рассчитывают."}
        </p>
        <fieldset disabled={pending || locked} className="space-y-4">
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
            <>
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
            </>
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
          <Button type="submit" disabled={pending}>
            {pending ? "Сохранение…" : error ? "Повторить запрос" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
