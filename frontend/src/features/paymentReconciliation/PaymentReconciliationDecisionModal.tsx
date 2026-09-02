import { isAxiosError } from "axios";
import { useEffect, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Checkbox, Input, Label, Modal, SegmentedControl } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { useConfirmReconciliation, useVoidReconciliation } from "./queries";
import { type PaymentReconciliationItem } from "./types";

type Decision = "confirm" | "void";

const safeText = (label: string, maxLength: number) =>
  z
    .string()
    .trim()
    .min(1, `Укажите ${label}`)
    .max(maxLength, `Не более ${maxLength} символов`)
    .refine(
      (value) =>
        Array.from(value).every((character) => {
          const code = character.charCodeAt(0);
          return code >= 32 && code !== 127;
        }),
      "Недопустимые символы",
    );

const formSchema = z.object({
  terminal_id: safeText("терминал", 64),
  external_reference: safeText("номер операции или документа", 128),
  operator_note: z.string().trim().max(160, "Не более 160 символов"),
  confirmed: z.literal(true, {
    errorMap: () => ({ message: "Подтвердите результат сверки" }),
  }),
});

interface FormValues {
  terminal_id: string;
  external_reference: string;
  operator_note: string;
  confirmed: boolean;
}

const decisionOptions = [
  { value: "confirm", label: "Оплата прошла" },
  { value: "void", label: "Оплаты нет" },
] as const;

export function PaymentReconciliationDecisionModal({
  item,
  onClose,
  onResolved,
}: {
  item: PaymentReconciliationItem;
  onClose: () => void;
  onResolved: () => void;
}): JSX.Element {
  const [decision, setDecision] = useState<Decision>("confirm");
  const [topError, setTopError] = useState<string | null>(null);
  const confirmMutation = useConfirmReconciliation();
  const voidMutation = useVoidReconciliation();
  const isLoading = confirmMutation.isPending || voidMutation.isPending;
  const form = useForm<FormValues>({
    defaultValues: {
      terminal_id: item.configured_terminal_id ?? "",
      external_reference: "",
      operator_note: "",
      confirmed: false,
    },
  });

  useEffect(() => {
    form.setValue("terminal_id", item.configured_terminal_id ?? "");
    form.setValue("confirmed", false);
    form.clearErrors();
    setTopError(null);
  }, [decision, form, item.configured_terminal_id]);

  const submit = form.handleSubmit(async (values) => {
    const parsed = formSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string") {
          form.setError(field as keyof FormValues, { message: issue.message });
        }
      }
      return;
    }

    setTopError(null);
    try {
      if (decision === "confirm") {
        await confirmMutation.mutateAsync({
          id: item.id,
          payload: {
            terminal_id: parsed.data.terminal_id,
            external_reference: parsed.data.external_reference,
          },
        });
      } else {
        await voidMutation.mutateAsync({
          id: item.id,
          payload: {
            reason: "manager_override",
            terminal_id: parsed.data.terminal_id,
            external_reference: parsed.data.external_reference,
            operator_note: parsed.data.operator_note || null,
          },
        });
      }
      onResolved();
    } catch (error) {
      setTopError(
        isAxiosError(error) && error.response?.status === 409
          ? "Операцию уже обработал другой сотрудник. Закройте окно и обновите очередь."
          : describeApiError(error, "Не удалось сохранить результат сверки"),
      );
    }
  });

  return (
    <Modal open onClose={onClose} title="Решение по оплате" className="max-w-xl">
      <form className="space-y-4" noValidate onSubmit={submit}>
        <div className="rounded-md border border-border bg-background p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="font-semibold">{item.register_name}</p>
            <p className="font-mono text-lg font-semibold">
              {formatMoney(item.amount)} {item.currency}
            </p>
          </div>
          <p className="mt-1 text-sm text-foreground-muted">
            {item.branch_name} · {item.cashier_name ?? "Кассир не указан"}
          </p>
        </div>

        <SegmentedControl
          value={decision}
          options={decisionOptions}
          onChange={setDecision}
          label="Результат сверки"
          className="w-full [&>button]:flex-1"
        />

        <p className="rounded-md border border-info/30 bg-info-subtle p-3 text-sm text-foreground-secondary">
          Проверьте чек или журнал терминала. Aurum не повторяет списание денег и только фиксирует
          подтверждённый вами результат.
        </p>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Терминал"
            htmlFor="reconciliation-terminal"
            error={form.formState.errors.terminal_id?.message}
          >
            <Input
              id="reconciliation-terminal"
              autoComplete="off"
              maxLength={64}
              placeholder="Например, TERM-01"
              invalid={Boolean(form.formState.errors.terminal_id)}
              {...form.register("terminal_id")}
            />
            {item.configured_terminal_id ? (
              <p className="mt-1.5 text-xs text-foreground-muted">
                Подставлен из настроек рабочей кассы. Сверьте с фактическим чеком.
              </p>
            ) : null}
          </Field>
          <Field
            label="Номер операции/документа"
            htmlFor="reconciliation-reference"
            error={form.formState.errors.external_reference?.message}
          >
            <Input
              id="reconciliation-reference"
              autoComplete="off"
              maxLength={128}
              placeholder="Из чека или журнала"
              invalid={Boolean(form.formState.errors.external_reference)}
              {...form.register("external_reference")}
            />
          </Field>
        </div>

        {decision === "void" ? (
          <Field
            label="Комментарий (необязательно)"
            htmlFor="reconciliation-note"
            error={form.formState.errors.operator_note?.message}
          >
            <Input
              id="reconciliation-note"
              autoComplete="off"
              maxLength={160}
              placeholder="Например, операции нет в журнале"
              invalid={Boolean(form.formState.errors.operator_note)}
              {...form.register("operator_note")}
            />
          </Field>
        ) : null}

        <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3 text-sm">
          <Checkbox {...form.register("confirmed")} />
          <span>
            Подтверждаю, что проверил журнал терминала и указал фактический результат оплаты
          </span>
        </label>
        {form.formState.errors.confirmed?.message ? (
          <p className="text-sm text-danger" role="alert">
            {form.formState.errors.confirmed.message}
          </p>
        ) : null}
        {topError ? (
          <p
            className="rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm text-danger"
            role="alert"
          >
            {topError}
          </p>
        ) : null}

        <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
          <Button type="button" variant="secondary" disabled={isLoading} onClick={onClose}>
            Отмена
          </Button>
          <Button
            type="submit"
            variant={decision === "confirm" ? "success" : "danger"}
            isLoading={isLoading}
          >
            {decision === "confirm" ? "Подтвердить оплату" : "Зафиксировать отсутствие оплаты"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function Field({
  label,
  htmlFor,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  error?: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <div>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? (
        <p className="mt-1.5 text-sm text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function formatMoney(value: string): string {
  const amount = Number(value);
  return Number.isFinite(amount)
    ? new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
        amount,
      )
    : value;
}
