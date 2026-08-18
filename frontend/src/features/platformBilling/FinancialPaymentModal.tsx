import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  FormError,
  Input,
  Label,
  Modal,
  Select,
  SkeletonRows,
  Textarea,
} from "@/components/ui";
import { formatBillingDate, formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";

import { createOperationId } from "./operationId";
import {
  platformBillingKeys,
  useApprovePlatformBankPayment,
  useCreatePlatformBankPaymentReview,
  usePlatformPaymentApprovalDetail,
  useRejectPlatformBankPaymentReview,
} from "./queries";
import {
  type PlatformFinancialInvoice,
  type PlatformPaymentApproval,
  type PlatformPaymentApprovalDetail,
  type PlatformPaymentApprovalQueueItem,
} from "./types";

const PRIMARY_TJS_ACCOUNT_KEY = "aurum_tjs_primary";
const moneyPattern = /^\d{1,12}(?:[.,]\d{1,2})?$/;

const reviewSchema = z.object({
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

type ReviewForm = z.infer<typeof reviewSchema>;
const EMPTY_REVIEW_FORM: ReviewForm = {
  target_invoice_id: "",
  amount: "",
  paid_at: "",
  external_reference: "",
};

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

  const close = () => {
    if (mutation.isPending) return;
    form.reset(EMPTY_REVIEW_FORM);
    setTopError(null);
    onClose();
  };

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
      form.reset(EMPTY_REVIEW_FORM);
      onCompleted("Платёж передан другому сотруднику на подтверждение.");
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        form.reset(EMPTY_REVIEW_FORM);
        setTopError(null);
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
      onClose={close}
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
            {...form.register("target_invoice_id", {
              onChange: (event: React.ChangeEvent<HTMLSelectElement>) => {
                const invoice = invoices.find((item) => item.invoice_id === event.target.value);
                form.setValue("amount", invoice?.outstanding_amount ?? "", {
                  shouldValidate: true,
                });
              },
            })}
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
        {!online ? (
          <p className="text-sm text-warning-foreground" role="status">
            Нет подключения. Регистрация платежа временно недоступна.
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="secondary" disabled={mutation.isPending} onClick={close}>
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

const reviewRejectionSchema = z
  .object({
    reason_code: z.enum([
      "bank_payment_not_found",
      "amount_mismatch",
      "date_mismatch",
      "duplicate",
      "wrong_tenant_or_invoice",
      "other",
    ]),
    reason_note: z.string().trim().max(500, "Не более 500 символов"),
  })
  .superRefine((value, context) => {
    if (value.reason_code === "other" && value.reason_note.length < 10) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["reason_note"],
        message: "Опишите причину минимум в 10 символах",
      });
    }
  });

type ReviewRejectionForm = z.infer<typeof reviewRejectionSchema>;

export function ApprovePaymentModal({
  item,
  online,
  onClose,
  onCompleted,
  onRejected,
  onRefreshRequired,
}: {
  item: PlatformPaymentApprovalQueueItem | null;
  online: boolean;
  onClose: () => void;
  onCompleted: (result: PlatformPaymentApproval) => void;
  onRejected: () => void;
  onRefreshRequired: (message: string) => void;
}): JSX.Element | null {
  const queryClient = useQueryClient();
  const mutation = useApprovePlatformBankPayment();
  const rejectionMutation = useRejectPlatformBankPaymentReview();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const [mode, setMode] = useState<"approve" | "reject">("approve");
  const form = useForm<ApprovalForm>({ defaultValues: { confirmed: false } });
  const rejectionForm = useForm<ReviewRejectionForm>({
    defaultValues: { reason_code: "bank_payment_not_found", reason_note: "" },
  });
  const tenantId = item?.tenant_id ?? "";
  const reviewId = item?.review_id ?? "";
  const detailQuery = usePlatformPaymentApprovalDetail(
    tenantId,
    reviewId,
    item !== null && !item.is_own_review,
  );

  const clearSensitiveDetail = useCallback(() => {
    if (!tenantId || !reviewId) return;
    queryClient.removeQueries({
      queryKey: platformBillingKeys.approvalDetail(tenantId, reviewId),
    });
  }, [queryClient, reviewId, tenantId]);

  useEffect(() => {
    if (!item) return;
    setOperationId(createOperationId());
    setTopError(null);
    setMode("approve");
    form.reset({ confirmed: false });
    rejectionForm.reset({ reason_code: "bank_payment_not_found", reason_note: "" });
  }, [form, item, rejectionForm]);

  useEffect(() => clearSensitiveDetail, [clearSensitiveDetail]);

  useEffect(() => {
    if (!item || item.is_own_review || !detailQuery.data) return;
    const hideSensitiveDetail = () => {
      clearSensitiveDetail();
      onClose();
      onRefreshRequired("Защищённые реквизиты скрыты. Откройте платёж повторно.");
    };
    const timeoutId = window.setTimeout(hideSensitiveDetail, 120_000);
    const handleVisibilityChange = () => {
      if (document.visibilityState !== "visible") hideSensitiveDetail();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.clearTimeout(timeoutId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [clearSensitiveDetail, detailQuery.data, item, onClose, onRefreshRequired]);

  if (!item) return null;

  const close = () => {
    if (mutation.isPending || rejectionMutation.isPending) return;
    clearSensitiveDetail();
    onClose();
  };

  const submit = form.handleSubmit(async (values) => {
    const detail = detailQuery.data;
    if (!detail) return;
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
        tenantId: detail.tenant_id,
        reviewId: detail.review_id,
        payload: { operation_id: operationId, expected_row_version: detail.row_version },
      });
      clearSensitiveDetail();
      onCompleted(result.item);
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        close();
        onRefreshRequired("Другой сотрудник уже обработал платёж. Очередь обновлена.");
        return;
      }
      setTopError(describeApiError(error, "Не удалось подтвердить платёж."));
    }
  });

  const reject = rejectionForm.handleSubmit(async (values) => {
    const detail = detailQuery.data;
    if (!detail) return;
    const parsed = reviewRejectionSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "reason_code" || field === "reason_note") {
          rejectionForm.setError(field, { message: issue.message });
        }
      }
      return;
    }
    if (!online) {
      setTopError("Нет подключения. Отклонение платежа временно отключено.");
      return;
    }
    setTopError(null);
    try {
      await rejectionMutation.mutateAsync({
        tenantId: detail.tenant_id,
        reviewId: detail.review_id,
        payload: {
          operation_id: operationId,
          expected_row_version: detail.row_version,
          reason_code: parsed.data.reason_code,
          reason_note: parsed.data.reason_note || null,
        },
      });
      clearSensitiveDetail();
      onRejected();
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        close();
        onRefreshRequired("Другой сотрудник уже обработал платёж. Очередь обновлена.");
        return;
      }
      setTopError(describeApiError(error, "Не удалось отклонить платёж."));
    }
  });

  const pending = mutation.isPending || rejectionMutation.isPending;
  const detail = detailQuery.data;

  return (
    <Modal
      open
      onClose={close}
      title={mode === "reject" ? "Отклонение платежа" : "Проверка платежа"}
    >
      {item.is_own_review ? (
        <div className="space-y-4">
          <p className="rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
            Этот платёж зарегистрирован вами. Для подтверждения нужен другой сотрудник.
          </p>
          <div className="flex justify-end">
            <Button variant="secondary" onClick={close}>
              Закрыть
            </Button>
          </div>
        </div>
      ) : detailQuery.isLoading ? (
        <SkeletonRows rows={5} />
      ) : detailQuery.error || !detail ? (
        <div className="space-y-4" role="alert">
          <p className="text-sm text-danger-foreground">
            {describeApiError(
              detailQuery.error,
              "Не удалось открыть защищённые реквизиты платежа.",
            )}
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={close}>
              Закрыть
            </Button>
            <Button isLoading={detailQuery.isFetching} onClick={() => void detailQuery.refetch()}>
              Повторить
            </Button>
          </div>
        </div>
      ) : mode === "reject" ? (
        <form className="space-y-4" noValidate onSubmit={reject}>
          <PaymentReviewSummary detail={detail} />
          {!online ? <OfflinePaymentNotice /> : null}
          <div>
            <Label htmlFor="financial-payment-rejection-reason">Причина</Label>
            <Select
              id="financial-payment-rejection-reason"
              autoFocus
              disabled={pending}
              {...rejectionForm.register("reason_code")}
            >
              <option value="bank_payment_not_found">Платёж не найден в банке</option>
              <option value="amount_mismatch">Сумма не совпадает</option>
              <option value="date_mismatch">Дата не совпадает</option>
              <option value="duplicate">Дубликат заявки</option>
              <option value="wrong_tenant_or_invoice">Другая аптека или счёт</option>
              <option value="other">Другая причина</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="financial-payment-rejection-note">Комментарий</Label>
            <Textarea
              id="financial-payment-rejection-note"
              rows={3}
              maxLength={500}
              disabled={pending}
              invalid={Boolean(rejectionForm.formState.errors.reason_note)}
              placeholder="Необязательно, кроме варианта «Другая причина»"
              {...rejectionForm.register("reason_note")}
            />
            <FormError>{rejectionForm.formState.errors.reason_note?.message}</FormError>
          </div>
          {topError ? (
            <p className="text-sm text-danger" role="alert">
              {topError}
            </p>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
            <Button
              type="button"
              variant="secondary"
              disabled={pending}
              onClick={() => {
                setTopError(null);
                setOperationId(createOperationId());
                setMode("approve");
              }}
            >
              Назад
            </Button>
            <Button type="submit" variant="danger" disabled={!online} isLoading={pending}>
              Отклонить платёж
            </Button>
          </div>
        </form>
      ) : (
        <form className="space-y-4" noValidate onSubmit={submit}>
          <PaymentReviewSummary detail={detail} />
          {!online ? <OfflinePaymentNotice /> : null}
          <p className="text-sm text-foreground-secondary">
            После подтверждения система сначала погасит самый старый долг, затем сохранит остаток
            как кредит аптеки. Действие потребует MFA.
          </p>
          <label className="flex min-h-11 items-start gap-3 rounded-lg border border-border px-3 py-2.5 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 h-5 w-5"
              disabled={pending}
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
            <Button type="button" variant="secondary" disabled={pending} onClick={close}>
              Отмена
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={!online || pending}
              onClick={() => {
                setTopError(null);
                setOperationId(createOperationId());
                setMode("reject");
              }}
            >
              Отклонить
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

function OfflinePaymentNotice(): JSX.Element {
  return (
    <p className="text-sm text-warning-foreground" role="status">
      Нет подключения. Денежное действие временно недоступно.
    </p>
  );
}

function PaymentReviewSummary({ detail }: { detail: PlatformPaymentApprovalDetail }): JSX.Element {
  return (
    <dl className="grid gap-3 rounded-lg border border-border bg-surface-subtle p-4 sm:grid-cols-2">
      <Summary label="Аптека" value={detail.tenant_name} />
      <Summary label="Счёт" value={detail.invoice_number} />
      <Summary label="Сумма" value={formatBillingMoney(detail.amount, detail.currency)} />
      <Summary label="Дата платежа" value={formatBillingDate(detail.paid_at)} />
      <Summary label="Счёт получателя" value="Основной счёт Aurum, TJS" />
      <div className="min-w-0 sm:col-span-2">
        <dt className="text-xs text-foreground-muted">Номер банковской операции</dt>
        <dd className="mt-1 break-all font-mono text-sm font-semibold text-foreground">
          {detail.external_reference}
        </dd>
      </div>
    </dl>
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
