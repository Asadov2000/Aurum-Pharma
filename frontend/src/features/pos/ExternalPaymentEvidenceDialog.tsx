import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Modal } from "@/components/ui";

import { type PaymentAttemptConfirmPayload } from "./types";

const evidenceValue = (label: string, maxLength: number) =>
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

const schema = z.object({
  terminal_id: evidenceValue("терминал", 64),
  external_reference: evidenceValue("номер операции или документа", 128),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  open: boolean;
  attemptId: string | null;
  method: "card" | "qr";
  amount: string;
  currency: string;
  isLoading: boolean;
  error?: string | null;
  onConfirm: (evidence: PaymentAttemptConfirmPayload) => void | Promise<void>;
  onDecline: (evidence: PaymentAttemptConfirmPayload) => void | Promise<void>;
}

export function ExternalPaymentEvidenceDialog({
  open,
  attemptId,
  method,
  amount,
  currency,
  isLoading,
  error,
  onConfirm,
  onDecline,
}: Props): JSX.Element {
  const form = useForm<FormValues>({
    defaultValues: { terminal_id: "", external_reference: "" },
  });

  useEffect(() => {
    if (open) {
      form.reset({ terminal_id: "", external_reference: "" });
    }
  }, [attemptId, form, open]);

  const submit = (action: Props["onConfirm"] | Props["onDecline"]) =>
    form.handleSubmit(async (values) => {
      form.clearErrors("root");
      const parsed = schema.safeParse(values);
      if (!parsed.success) {
        const firstIssue = parsed.error.issues[0];
        const field = firstIssue?.path[0];
        if (firstIssue && (field === "terminal_id" || field === "external_reference")) {
          form.setError(field, { message: firstIssue.message });
          form.setFocus(field);
        }
        return;
      }
      await action(parsed.data);
    });

  return (
    <Modal
      open={open}
      onClose={() =>
        form.setError("root", {
          message: "Сначала укажите реквизиты и зафиксируйте результат операции терминала.",
        })
      }
      title={method === "qr" ? "Сверка оплаты QR" : "Сверка оплаты картой"}
    >
      <form className="space-y-4" noValidate onSubmit={submit(onConfirm)}>
        <div className="rounded-md border border-info/30 bg-info-subtle p-3 text-sm text-foreground-secondary">
          <p className="font-semibold text-foreground">
            {Number(amount).toFixed(2)} {currency}
          </p>
          <p className="mt-1">
            Возьмите данные с чека или из журнала терминала. Aurum только фиксирует результат и сам
            деньги не списывает.
          </p>
        </div>

        <div>
          <label
            className="mb-1.5 block text-xs font-semibold text-foreground-secondary"
            htmlFor="external-payment-terminal"
          >
            Терминал
          </label>
          <Input
            id="external-payment-terminal"
            autoComplete="off"
            maxLength={64}
            placeholder="Например, TERM-01"
            invalid={Boolean(form.formState.errors.terminal_id)}
            {...form.register("terminal_id")}
          />
          {form.formState.errors.terminal_id?.message ? (
            <p className="mt-1.5 text-sm text-danger" role="alert">
              {form.formState.errors.terminal_id.message}
            </p>
          ) : null}
        </div>

        <div>
          <label
            className="mb-1.5 block text-xs font-semibold text-foreground-secondary"
            htmlFor="external-payment-reference"
          >
            Номер операции/документа
          </label>
          <Input
            id="external-payment-reference"
            autoComplete="off"
            maxLength={128}
            placeholder="Номер с чека или журнала терминала"
            invalid={Boolean(form.formState.errors.external_reference)}
            {...form.register("external_reference")}
          />
          {form.formState.errors.external_reference?.message ? (
            <p className="mt-1.5 text-sm text-danger" role="alert">
              {form.formState.errors.external_reference.message}
            </p>
          ) : null}
        </div>

        {form.formState.errors.root?.message ?? error ? (
          <p className="mt-1.5 text-sm text-danger" role="alert">
            {form.formState.errors.root?.message ?? error}
          </p>
        ) : null}

        <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="secondary"
            disabled={isLoading}
            onClick={() => void submit(onDecline)()}
          >
            Оплаты нет
          </Button>
          <Button type="submit" variant="success" isLoading={isLoading}>
            Оплата прошла
          </Button>
        </div>
      </form>
    </Modal>
  );
}
