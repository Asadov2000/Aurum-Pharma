import { useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Modal, Select, Textarea } from "@/components/ui";
import { formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";

import { createOperationId } from "./operationId";
import {
  useApprovePlatformPaymentAdjustment,
  useCreatePlatformPaymentAdjustment,
  useRejectPlatformPaymentAdjustment,
} from "./queries";
import {
  type PlatformPaymentAdjustmentApproval,
  type PlatformPaymentAdjustmentQueueItem,
  type PlatformPaymentHistoryItem,
} from "./types";

const moneyPattern = /^\d{1,12}(?:[.,]\d{1,2})?$/;

const adjustmentSchema = z
  .object({
    adjustment_kind: z.enum(["correction", "bank_refund"]),
    amount: z.string().trim().regex(moneyPattern, "Введите сумму с точностью до 2 знаков"),
    reason_code: z.enum([
      "payment_entered_in_error",
      "amount_correction",
      "bank_refund_completed",
      "contract_resolution",
      "other",
    ]),
    reason_note: z.string().trim().min(10, "Минимум 10 символов").max(500),
    refunded_at: z.string(),
    refund_reference: z.string().trim().max(128),
  })
  .superRefine((value, context) => {
    if (value.adjustment_kind === "correction") {
      if (
        !(["payment_entered_in_error", "amount_correction", "other"] as string[]).includes(
          value.reason_code,
        )
      ) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["reason_code"],
          message: "Выберите причину корректировки",
        });
      }
      return;
    }
    if (
      !(["bank_refund_completed", "contract_resolution", "other"] as string[]).includes(
        value.reason_code,
      )
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["reason_code"],
        message: "Выберите причину возврата",
      });
    }
    if (!value.refunded_at) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["refunded_at"],
        message: "Укажите дату возврата",
      });
    }
    if (
      !/^[A-Za-z0-9\s\-_/]+$/.test(value.refund_reference) ||
      value.refund_reference.replace(/[^A-Za-z0-9]/g, "").length < 4
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["refund_reference"],
        message: "Укажите банковский номер из латинских букв и цифр",
      });
    }
  });

type AdjustmentForm = z.infer<typeof adjustmentSchema>;

export function PaymentAdjustmentModal({
  tenantId,
  payment,
  online,
  onClose,
  onCompleted,
  onRefreshRequired,
}: {
  tenantId: string;
  payment: PlatformPaymentHistoryItem | null;
  online: boolean;
  onClose: () => void;
  onCompleted: () => void;
  onRefreshRequired: (message: string) => void;
}): JSX.Element | null {
  const mutation = useCreatePlatformPaymentAdjustment();
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const form = useForm<AdjustmentForm>({
    defaultValues: {
      adjustment_kind: "correction",
      amount: "",
      reason_code: "payment_entered_in_error",
      reason_note: "",
      refunded_at: "",
      refund_reference: "",
    },
  });
  const kind = form.watch("adjustment_kind");

  useEffect(() => {
    if (!payment) return;
    setOperationId(createOperationId());
    setTopError(null);
    form.reset({
      adjustment_kind: "correction",
      amount: payment.reversible_amount,
      reason_code: "payment_entered_in_error",
      reason_note: "",
      refunded_at: "",
      refund_reference: "",
    });
  }, [form, payment]);

  useEffect(() => {
    if (!payment) return;
    if (kind === "correction") {
      form.setValue("amount", payment.reversible_amount);
      form.setValue("reason_code", "payment_entered_in_error");
      form.setValue("refunded_at", "");
      form.setValue("refund_reference", "");
    } else {
      form.setValue("reason_code", "bank_refund_completed");
    }
    form.clearErrors();
  }, [form, kind, payment]);

  if (!payment) return null;

  const submit = form.handleSubmit(async (values) => {
    const parsed = adjustmentSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string" && field in values) {
          form.setError(field as keyof AdjustmentForm, { message: issue.message });
        }
      }
      return;
    }
    if (!online) {
      setTopError("Нет подключения. Финансовые изменения временно отключены.");
      return;
    }
    let refundedAt: string | null = null;
    if (parsed.data.adjustment_kind === "bank_refund") {
      const date = new Date(parsed.data.refunded_at);
      if (Number.isNaN(date.getTime())) {
        form.setError("refunded_at", { message: "Проверьте дату и время" });
        return;
      }
      refundedAt = date.toISOString();
    }
    setTopError(null);
    try {
      await mutation.mutateAsync({
        tenantId,
        paymentId: payment.payment_id,
        payload: {
          operation_id: operationId,
          adjustment_kind: parsed.data.adjustment_kind,
          amount: parsed.data.amount.replace(",", "."),
          reason_code: parsed.data.reason_code,
          reason_note: parsed.data.reason_note,
          refunded_at: refundedAt,
          refund_reference:
            parsed.data.adjustment_kind === "bank_refund" ? parsed.data.refund_reference : null,
        },
      });
      form.reset();
      onCompleted();
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        form.reset();
        onClose();
        onRefreshRequired("Платёж уже изменён или ожидает решения. Данные обновлены.");
        return;
      }
      setTopError(describeApiError(error, "Не удалось создать запрос корректировки."));
    }
  });

  return (
    <Modal open onClose={() => !mutation.isPending && onClose()} title="Корректировка платежа">
      <form className="space-y-4" noValidate onSubmit={submit}>
        <dl className="grid gap-3 rounded-lg border border-border bg-surface-subtle p-4 sm:grid-cols-2">
          <Summary label="Платёж" value={formatBillingMoney(payment.amount, payment.currency)} />
          <Summary
            label="Доступно"
            value={formatBillingMoney(payment.reversible_amount, payment.currency)}
          />
        </dl>
        <div>
          <Label htmlFor="payment-adjustment-kind">Операция</Label>
          <Select
            id="payment-adjustment-kind"
            autoFocus
            disabled={mutation.isPending}
            {...form.register("adjustment_kind")}
          >
            <option value="correction">Исправить ошибочно учтённый платёж</option>
            <option value="bank_refund">Зафиксировать возврат через банк</option>
          </Select>
        </div>
        {kind === "bank_refund" ? (
          <div className="rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
            Aurum только фиксирует уже выполненный возврат и не отправляет деньги через банк.
          </div>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="payment-adjustment-amount">Сумма, TJS</Label>
            <Input
              id="payment-adjustment-amount"
              inputMode="decimal"
              autoComplete="off"
              disabled={mutation.isPending || kind === "correction"}
              invalid={Boolean(form.formState.errors.amount)}
              {...form.register("amount")}
            />
            <FormError>{form.formState.errors.amount?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="payment-adjustment-reason">Причина</Label>
            <Select
              id="payment-adjustment-reason"
              disabled={mutation.isPending}
              invalid={Boolean(form.formState.errors.reason_code)}
              {...form.register("reason_code")}
            >
              {kind === "correction" ? (
                <>
                  <option value="payment_entered_in_error">Платёж внесён ошибочно</option>
                  <option value="amount_correction">Ошибка суммы</option>
                </>
              ) : (
                <>
                  <option value="bank_refund_completed">Возврат выполнен банком</option>
                  <option value="contract_resolution">Решение по договору</option>
                </>
              )}
              <option value="other">Другая причина</option>
            </Select>
            <FormError>{form.formState.errors.reason_code?.message}</FormError>
          </div>
        </div>
        {kind === "bank_refund" ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="payment-adjustment-refunded-at">Дата возврата</Label>
              <Input
                id="payment-adjustment-refunded-at"
                type="datetime-local"
                disabled={mutation.isPending}
                invalid={Boolean(form.formState.errors.refunded_at)}
                {...form.register("refunded_at")}
              />
              <FormError>{form.formState.errors.refunded_at?.message}</FormError>
            </div>
            <div>
              <Label htmlFor="payment-adjustment-reference">Банковский номер</Label>
              <Input
                id="payment-adjustment-reference"
                autoComplete="off"
                spellCheck={false}
                disabled={mutation.isPending}
                invalid={Boolean(form.formState.errors.refund_reference)}
                {...form.register("refund_reference")}
              />
              <FormError>{form.formState.errors.refund_reference?.message}</FormError>
            </div>
          </div>
        ) : null}
        <div>
          <Label htmlFor="payment-adjustment-note">Обоснование</Label>
          <Textarea
            id="payment-adjustment-note"
            rows={3}
            maxLength={500}
            disabled={mutation.isPending}
            invalid={Boolean(form.formState.errors.reason_note)}
            {...form.register("reason_note")}
          />
          <FormError>{form.formState.errors.reason_note?.message}</FormError>
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

const decisionSchema = z.object({
  confirmed: z.boolean().refine((value) => value, "Подтвердите независимую проверку"),
});

const rejectionSchema = z
  .object({
    reason_code: z.enum([
      "bank_refund_not_verified",
      "amount_mismatch",
      "request_not_supported",
      "duplicate",
      "other",
    ]),
    reason_note: z.string().trim().max(500),
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

type DecisionForm = z.infer<typeof decisionSchema>;
type RejectionForm = z.infer<typeof rejectionSchema>;

export function PaymentAdjustmentDecisionModal({
  item,
  online,
  onClose,
  onApproved,
  onRejected,
  onRefreshRequired,
}: {
  item: PlatformPaymentAdjustmentQueueItem | null;
  online: boolean;
  onClose: () => void;
  onApproved: (result: PlatformPaymentAdjustmentApproval) => void;
  onRejected: () => void;
  onRefreshRequired: (message: string) => void;
}): JSX.Element | null {
  const approval = useApprovePlatformPaymentAdjustment();
  const rejection = useRejectPlatformPaymentAdjustment();
  const [mode, setMode] = useState<"approve" | "reject">("approve");
  const [operationId, setOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const decisionForm = useForm<DecisionForm>({ defaultValues: { confirmed: false } });
  const rejectionForm = useForm<RejectionForm>({
    defaultValues: { reason_code: "bank_refund_not_verified", reason_note: "" },
  });

  useEffect(() => {
    if (!item) return;
    setMode("approve");
    setOperationId(createOperationId());
    setTopError(null);
    decisionForm.reset({ confirmed: false });
    rejectionForm.reset({ reason_code: "bank_refund_not_verified", reason_note: "" });
  }, [decisionForm, item, rejectionForm]);

  if (!item) return null;
  const pending = approval.isPending || rejection.isPending;
  const handleConflict = (error: unknown): boolean => {
    if (!isAxiosError(error) || error.response?.status !== 409) return false;
    onClose();
    onRefreshRequired("Другой сотрудник уже обработал запрос. Очередь обновлена.");
    return true;
  };
  const approve = decisionForm.handleSubmit(async (values) => {
    const parsed = decisionSchema.safeParse(values);
    if (!parsed.success) {
      decisionForm.setError("confirmed", { message: parsed.error.issues[0]?.message });
      return;
    }
    if (!online) return setTopError("Нет подключения. Подтверждение временно отключено.");
    setTopError(null);
    try {
      const result = await approval.mutateAsync({
        tenantId: item.tenant_id,
        adjustmentId: item.adjustment_id,
        payload: { operation_id: operationId, expected_row_version: item.row_version },
      });
      onApproved(result.item);
    } catch (error) {
      if (!handleConflict(error)) {
        setTopError(describeApiError(error, "Не удалось подтвердить корректировку."));
      }
    }
  });
  const reject = rejectionForm.handleSubmit(async (values) => {
    const parsed = rejectionSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "reason_code" || field === "reason_note") {
          rejectionForm.setError(field, { message: issue.message });
        }
      }
      return;
    }
    if (!online) return setTopError("Нет подключения. Отклонение временно отключено.");
    setTopError(null);
    try {
      await rejection.mutateAsync({
        tenantId: item.tenant_id,
        adjustmentId: item.adjustment_id,
        payload: {
          operation_id: operationId,
          expected_row_version: item.row_version,
          reason_code: parsed.data.reason_code,
          reason_note: parsed.data.reason_note || null,
        },
      });
      onRejected();
    } catch (error) {
      if (!handleConflict(error)) {
        setTopError(describeApiError(error, "Не удалось отклонить корректировку."));
      }
    }
  });

  return (
    <Modal
      open
      onClose={() => !pending && onClose()}
      title={mode === "reject" ? "Отклонение корректировки" : "Проверка корректировки"}
    >
      {item.is_own_request ? (
        <div className="space-y-4">
          <p className="rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
            Запрос создан вами. Решение должен принять другой сотрудник.
          </p>
          <div className="flex justify-end">
            <Button variant="secondary" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      ) : mode === "reject" ? (
        <form className="space-y-4" noValidate onSubmit={reject}>
          <AdjustmentSummary item={item} />
          <div>
            <Label htmlFor="adjustment-rejection-reason">Причина</Label>
            <Select
              id="adjustment-rejection-reason"
              autoFocus
              disabled={pending}
              {...rejectionForm.register("reason_code")}
            >
              <option value="bank_refund_not_verified">Возврат не подтверждён банком</option>
              <option value="amount_mismatch">Сумма не совпадает</option>
              <option value="request_not_supported">Недостаточно оснований</option>
              <option value="duplicate">Дубликат</option>
              <option value="other">Другая причина</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="adjustment-rejection-note">Комментарий</Label>
            <Textarea
              id="adjustment-rejection-note"
              rows={3}
              maxLength={500}
              disabled={pending}
              invalid={Boolean(rejectionForm.formState.errors.reason_note)}
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
              Отклонить
            </Button>
          </div>
        </form>
      ) : (
        <form className="space-y-4" noValidate onSubmit={approve}>
          <AdjustmentSummary item={item} />
          <p className="text-sm text-foreground-secondary">{item.reason_note}</p>
          <label className="flex min-h-11 items-start gap-3 rounded-lg border border-border px-3 py-2.5 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 h-5 w-5"
              disabled={pending}
              {...decisionForm.register("confirmed")}
            />
            <span>
              Я независимо проверил сумму, основание и
              {item.adjustment_kind === "bank_refund"
                ? " факт возврата в банке."
                : " исходный платёж."}
            </span>
          </label>
          <FormError>{decisionForm.formState.errors.confirmed?.message}</FormError>
          {topError ? (
            <p className="text-sm text-danger" role="alert">
              {topError}
            </p>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
            <Button type="button" variant="secondary" disabled={pending} onClick={onClose}>
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
              isLoading={approval.isPending}
            >
              Подтвердить
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function AdjustmentSummary({ item }: { item: PlatformPaymentAdjustmentQueueItem }): JSX.Element {
  return (
    <dl className="grid gap-3 rounded-lg border border-border bg-surface-subtle p-4 sm:grid-cols-2">
      <Summary label="Аптека" value={item.tenant_name} />
      <Summary
        label="Операция"
        value={item.adjustment_kind === "bank_refund" ? "Возврат через банк" : "Корректировка"}
      />
      <Summary label="Сумма" value={formatBillingMoney(item.amount, item.currency)} />
      <Summary
        label="Исходный платёж"
        value={formatBillingMoney(item.payment_amount, item.currency)}
      />
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
