import { useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal, Select } from "@/components/ui";
import { formatBillingDate, formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";

import { createOperationId } from "./operationId";
import { useApprovePlatformBankPayment, useCreatePlatformBankPaymentReview } from "./queries";
import {
  type PlatformFinancialInvoice,
  type PlatformPaymentApproval,
  type PlatformPaymentApprovalQueueItem,
} from "./types";

const PRIMARY_TJS_ACCOUNT_KEY = "aurum_tjs_primary";
const moneyPattern = /^\d{1,12}(?:[.,]\d{1,2})?$/;

const reviewSchema = z.object({
  target_invoice_id: z.string().min(1, "Выберите счёт"),
  amount: z.string().trim().regex(moneyPattern, "Введите сумму с точностью до 2 знаков"),
  paid_at: z.string().min(1, "Укажите дату и время платежа"),
  external_reference: z
    .string()
    .trim()
    .min(4, "Минимум 4 символа")
    .max(128, "Не более 128 символов")
    .refine(
      (value) =>
        /^[A-Za-z0-9\s\-_/]+$/.test(value) && value.replace(/[^A-Za-z0-9]/g, "").length >= 4,
      "Используйте латинские буквы и цифры",
    ),
});

type ReviewForm = z.infer<typeof reviewSchema>;

export function RegisterPaymentModal({
  open,
  tenantId,
  invoices,
  online,
  onClose,
  onCompleted,
  onRefreshRequired,
}: {
  open: boolean;
  tenantId: string;
  invoices: readonly PlatformFinancialInvoice[];
  online: boolean;
  onClose: () => void;
  onCompleted: (message: string) => void;
  onRefreshRequired: (message: string) => void;
}): JSX.Element {
  const mutation = useCreatePlatformBankPaymentReview();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<ReviewForm>({
    defaultValues: {
      target_invoice_id: "",
      amount: "",
      paid_at: localDateTimeValue(),
      external_reference: "",
    },
  });

  useEffect(() => {
    if (!open) return;
    const firstInvoice = invoices.find(
      (invoice) => invoice.document_state === "issued" && Number(invoice.outstanding_amount) > 0,
    );
    setOperationId(createOperationId());
    setTopError(null);
    form.reset({
      target_invoice_id: firstInvoice?.invoice_id ?? "",
      amount: firstInvoice?.outstanding_amount ?? "",
      paid_at: localDateTimeValue(),
      external_reference: "",
    });
  }, [form, invoices, open]);

  const submit = form.handleSubmit(async (values) => {
    if (!online) {
      setTopError("Нет подключения. Регистрация платежа временно отключена.");
      return;
    }
    const parsed = reviewSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string" && field in values) {
          form.setError(field as keyof ReviewForm, { message: issue.message });
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
        tenantId,
        payload: {
          operation_id: operationId,
          target_invoice_id: parsed.data.target_invoice_id,
          amount: parsed.data.amount.replace(",", "."),
          paid_at: paidAt.toISOString(),
          recipient_account_key: PRIMARY_TJS_ACCOUNT_KEY,
          external_reference: parsed.data.external_reference,
        },
      });
      form.reset();
      onCompleted("Платёж передан другому сотруднику на подтверждение.");
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        form.reset();
        onClose();
        onRefreshRequired("Платёж уже зарегистрирован или изменён. Данные обновлены.");
        return;
      }
      setTopError(describeApiError(error, "Не удалось передать платёж на подтверждение."));
    }
  });

  return (
    <Modal
      open={open}
      onClose={() => !mutation.isPending && onClose()}
      title="Регистрация банковского платежа"
    >
      <form className="space-y-4" noValidate onSubmit={submit}>
        <div className="rounded-lg border border-info/25 bg-info-subtle px-3 py-2 text-sm text-info-foreground">
          Система зарегистрирует заявление. Деньги будут учтены только после проверки другим
          сотрудником.
        </div>
        <div>
          <Label htmlFor="financial-payment-invoice">Счёт</Label>
          <Select
            id="financial-payment-invoice"
            autoFocus
            disabled={mutation.isPending}
            invalid={Boolean(form.formState.errors.target_invoice_id)}
            {...form.register("target_invoice_id")}
          >
            <option value="">Выберите счёт</option>
            {invoices
              .filter(
                (invoice) =>
                  invoice.document_state === "issued" && Number(invoice.outstanding_amount) > 0,
              )
              .map((invoice) => (
                <option key={invoice.invoice_id} value={invoice.invoice_id}>
                  {invoice.invoice_number} · {formatBillingMoney(invoice.outstanding_amount, "TJS")}
                </option>
              ))}
          </Select>
          <FormError>{form.formState.errors.target_invoice_id?.message}</FormError>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="financial-payment-amount">Сумма, TJS</Label>
            <Input
              id="financial-payment-amount"
              inputMode="decimal"
              autoComplete="off"
              disabled={mutation.isPending}
              invalid={Boolean(form.formState.errors.amount)}
              {...form.register("amount")}
            />
            <FormError>{form.formState.errors.amount?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="financial-payment-paid-at">Дата и время платежа</Label>
            <Input
              id="financial-payment-paid-at"
              type="datetime-local"
              disabled={mutation.isPending}
              invalid={Boolean(form.formState.errors.paid_at)}
              {...form.register("paid_at")}
            />
            <FormError>{form.formState.errors.paid_at?.message}</FormError>
          </div>
        </div>
        <div>
          <Label htmlFor="financial-payment-reference">Банковский номер операции</Label>
          <Input
            id="financial-payment-reference"
            autoComplete="off"
            spellCheck={false}
            placeholder="Например, TJ-2026-000125"
            disabled={mutation.isPending}
            invalid={Boolean(form.formState.errors.external_reference)}
            {...form.register("external_reference")}
          />
          <p className="mt-1 text-xs text-foreground-muted">
            После отправки номер будет удалён из формы и не появится в очереди.
          </p>
          <FormError>{form.formState.errors.external_reference?.message}</FormError>
        </div>
        {topError ? (
          <p className="text-sm text-danger" role="alert">
            {topError}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="secondary" disabled={mutation.isPending} onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" disabled={!online} isLoading={mutation.isPending}>
            Передать на подтверждение
          </Button>
        </div>
      </form>
    </Modal>
  );
}

const approvalSchema = z.object({
  confirmed: z.boolean().refine((value) => value, "Подтвердите результат проверки"),
});

type ApprovalForm = z.infer<typeof approvalSchema>;

export function ApprovePaymentModal({
  item,
  online,
  onClose,
  onCompleted,
  onRefreshRequired,
}: {
  item: PlatformPaymentApprovalQueueItem | null;
  online: boolean;
  onClose: () => void;
  onCompleted: (result: PlatformPaymentApproval) => void;
  onRefreshRequired: (message: string) => void;
}): JSX.Element | null {
  const mutation = useApprovePlatformBankPayment();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<ApprovalForm>({ defaultValues: { confirmed: false } });

  useEffect(() => {
    if (!item) return;
    setOperationId(createOperationId());
    setTopError(null);
    form.reset({ confirmed: false });
  }, [form, item]);

  if (!item) return null;

  const submit = form.handleSubmit(async (values) => {
    const parsed = approvalSchema.safeParse(values);
    if (!parsed.success) {
      form.setError("confirmed", { message: parsed.error.issues[0]?.message });
      return;
    }
    if (!online) {
      setTopError("Нет подключения. Подтверждение платежа временно отключено.");
      return;
    }
    setTopError(null);
    try {
      const result = await mutation.mutateAsync({
        tenantId: item.tenant_id,
        reviewId: item.review_id,
        payload: { operation_id: operationId, expected_row_version: item.row_version },
      });
      onCompleted(result.item);
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        onClose();
        onRefreshRequired("Другой сотрудник уже обработал платёж. Очередь обновлена.");
        return;
      }
      setTopError(describeApiError(error, "Не удалось подтвердить платёж."));
    }
  });

  return (
    <Modal open onClose={() => !mutation.isPending && onClose()} title="Подтверждение платежа">
      {item.is_own_review ? (
        <div className="space-y-4">
          <p className="rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
            Этот платёж зарегистрирован вами. Для подтверждения нужен другой сотрудник.
          </p>
          <div className="flex justify-end">
            <Button variant="secondary" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      ) : (
        <form className="space-y-4" noValidate onSubmit={submit}>
          <dl className="grid gap-3 rounded-lg border border-border bg-surface-subtle p-4 sm:grid-cols-2">
            <Summary label="Аптека" value={item.tenant_name} />
            <Summary label="Счёт" value={item.invoice_number} />
            <Summary label="Сумма" value={formatBillingMoney(item.amount, item.currency)} />
            <Summary label="Дата платежа" value={formatBillingDate(item.paid_at)} />
          </dl>
          <p className="text-sm text-foreground-secondary">
            После подтверждения система сначала погасит самый старый долг, затем сохранит остаток
            как кредит аптеки. Действие потребует MFA.
          </p>
          <label className="flex min-h-11 items-start gap-3 rounded-lg border border-border px-3 py-2.5 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 h-5 w-5"
              disabled={mutation.isPending}
              {...form.register("confirmed")}
            />
            <span>Я независимо сверил сумму и дату с банковской системой.</span>
          </label>
          <FormError>{form.formState.errors.confirmed?.message}</FormError>
          {topError ? (
            <p className="text-sm text-danger" role="alert">
              {topError}
            </p>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
            <Button
              type="button"
              variant="secondary"
              disabled={mutation.isPending}
              onClick={onClose}
            >
              Отмена
            </Button>
            <Button
              type="submit"
              variant="success"
              disabled={!online}
              isLoading={mutation.isPending}
            >
              Подтвердить платёж
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function Summary({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div>
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className="mt-1 break-words font-medium text-foreground">{value}</dd>
    </div>
  );
}

function localDateTimeValue(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}
