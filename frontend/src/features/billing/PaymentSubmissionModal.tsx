import { useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal, Select } from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { formatBillingMoney } from "./format";
import { createOperationId } from "./operationId";
import { useCreatePaymentSubmission } from "./queries";
import { type TenantFinancialInvoice } from "./types";

const moneyPattern = /^\d{1,12}(?:[.,]\d{1,2})?$/;
const submissionSchema = z.object({
  target_invoice_id: z.string().min(1, "Выберите счёт"),
  amount: z
    .string()
    .trim()
    .regex(moneyPattern, "Введите сумму с точностью до 2 знаков")
    .refine(
      (value) => !moneyPattern.test(value) || Number(value.replace(",", ".")) > 0,
      "Сумма должна быть больше нуля",
    ),
  paid_at: z.string().min(1, "Укажите дату и время платежа"),
  external_reference: z
    .string()
    .trim()
    .min(8, "Минимум 8 символов")
    .max(128, "Не более 128 символов")
    .refine(
      (value) =>
        /^[A-Za-z0-9\s\-_/]+$/.test(value) && value.replace(/[^A-Za-z0-9]/g, "").length >= 8,
      "Используйте латинские буквы и цифры",
    ),
});

type SubmissionForm = z.infer<typeof submissionSchema>;

const EMPTY_FORM: SubmissionForm = {
  target_invoice_id: "",
  amount: "",
  paid_at: "",
  external_reference: "",
};

export function PaymentSubmissionModal({
  open,
  invoices,
  initialInvoiceId,
  online,
  onClose,
  onCompleted,
  onRefreshRequired,
}: {
  open: boolean;
  invoices: readonly TenantFinancialInvoice[];
  initialInvoiceId: string | null;
  online: boolean;
  onClose: () => void;
  onCompleted: () => void;
  onRefreshRequired: (message: string) => void;
}): JSX.Element {
  const mutation = useCreatePaymentSubmission();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<SubmissionForm>({ defaultValues: EMPTY_FORM });

  useEffect(() => {
    if (!open) return;
    const openInvoices = invoices.filter(isOpenInvoice);
    const selectedInvoice =
      openInvoices.find((invoice) => invoice.invoice_id === initialInvoiceId) ?? openInvoices[0];
    setOperationId(createOperationId());
    setTopError(null);
    form.reset({
      target_invoice_id: selectedInvoice?.invoice_id ?? "",
      amount: selectedInvoice?.outstanding_amount ?? "",
      paid_at: localDateTimeValue(),
      external_reference: "",
    });
  }, [form, initialInvoiceId, invoices, open]);

  const close = () => {
    if (mutation.isPending) return;
    form.reset(EMPTY_FORM);
    setTopError(null);
    onClose();
  };

  const submit = form.handleSubmit(async (values) => {
    if (!online) {
      setTopError("Нет подключения. Отправка подтверждения временно недоступна.");
      return;
    }
    const parsed = submissionSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string" && field in values) {
          form.setError(field as keyof SubmissionForm, { message: issue.message });
        }
      }
      return;
    }
    const paidAt = new Date(parsed.data.paid_at);
    if (Number.isNaN(paidAt.getTime())) {
      form.setError("paid_at", { message: "Проверьте дату и время" });
      return;
    }

    setTopError(null);
    try {
      await mutation.mutateAsync({
        operation_id: operationId,
        target_invoice_id: parsed.data.target_invoice_id,
        amount: parsed.data.amount.replace(",", "."),
        paid_at: paidAt.toISOString(),
        external_reference: parsed.data.external_reference,
      });
      form.reset(EMPTY_FORM);
      onCompleted();
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        form.reset(EMPTY_FORM);
        onClose();
        onRefreshRequired("Заявка уже отправлена или данные счёта изменились. История обновлена.");
        return;
      }
      setTopError(describeApiError(error, "Не удалось отправить подтверждение оплаты."));
    }
  });

  const selectedInvoiceId = form.watch("target_invoice_id");
  const selectedInvoice = invoices.find((invoice) => invoice.invoice_id === selectedInvoiceId);

  return (
    <Modal open={open} onClose={close} title="Подтверждение банковской оплаты">
      <form className="space-y-4" noValidate onSubmit={submit}>
        <p className="rounded-lg border border-info/25 bg-info-subtle px-3 py-2 text-sm text-info-foreground">
          Заявка не зачисляет деньги автоматически. Платёж появится в балансе после проверки Aurum
          Pharma.
        </p>

        <div>
          <Label htmlFor="billing-submission-invoice">Счёт</Label>
          <Select
            id="billing-submission-invoice"
            autoFocus
            disabled={mutation.isPending}
            invalid={Boolean(form.formState.errors.target_invoice_id)}
            {...form.register("target_invoice_id", {
              onChange: (event: React.ChangeEvent<HTMLSelectElement>) => {
                const invoice = invoices.find((item) => item.invoice_id === event.target.value);
                if (invoice) form.setValue("amount", invoice.outstanding_amount);
              },
            })}
          >
            <option value="">Выберите счёт</option>
            {invoices.filter(isOpenInvoice).map((invoice) => (
              <option key={invoice.invoice_id} value={invoice.invoice_id}>
                {invoice.invoice_number} · {formatBillingMoney(invoice.outstanding_amount, "TJS")}
              </option>
            ))}
          </Select>
          <FormError>{form.formState.errors.target_invoice_id?.message}</FormError>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="billing-submission-amount">Сумма, TJS</Label>
            <Input
              id="billing-submission-amount"
              inputMode="decimal"
              autoComplete="off"
              disabled={mutation.isPending}
              invalid={Boolean(form.formState.errors.amount)}
              {...form.register("amount")}
            />
            <FormError>{form.formState.errors.amount?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="billing-submission-paid-at">Дата и время оплаты</Label>
            <Input
              id="billing-submission-paid-at"
              type="datetime-local"
              disabled={mutation.isPending}
              invalid={Boolean(form.formState.errors.paid_at)}
              {...form.register("paid_at")}
            />
            <FormError>{form.formState.errors.paid_at?.message}</FormError>
          </div>
        </div>

        <div>
          <Label htmlFor="billing-submission-reference">Номер банковской операции</Label>
          <Input
            id="billing-submission-reference"
            autoComplete="off"
            spellCheck={false}
            placeholder="Например, TJ-2026-000125"
            disabled={mutation.isPending}
            invalid={Boolean(form.formState.errors.external_reference)}
            {...form.register("external_reference")}
          />
          <p className="mt-1 text-xs text-foreground-muted">
            Полный номер используется только для защищённой проверки и не показывается в истории.
          </p>
          <FormError>{form.formState.errors.external_reference?.message}</FormError>
        </div>

        {selectedInvoice && Number(form.watch("amount").replace(",", ".")) > Number(selectedInvoice.outstanding_amount) ? (
          <p className="text-xs text-warning-foreground">
            Сумма превышает остаток счёта. После подтверждения излишек может стать авансом аптеки.
          </p>
        ) : null}

        {!online ? (
          <p className="text-sm text-warning-foreground" role="status">
            Нет подключения. Отправка заявки временно недоступна.
          </p>
        ) : null}

        {topError ? (
          <p className="text-sm text-danger" role="alert">
            {topError}
          </p>
        ) : null}

        <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="secondary" disabled={mutation.isPending} onClick={close}>
            Отмена
          </Button>
          <Button type="submit" disabled={!online} isLoading={mutation.isPending}>
            Отправить на проверку
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function isOpenInvoice(invoice: TenantFinancialInvoice): boolean {
  return invoice.document_state === "issued" && Number(invoice.outstanding_amount) > 0;
}

function localDateTimeValue(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}
