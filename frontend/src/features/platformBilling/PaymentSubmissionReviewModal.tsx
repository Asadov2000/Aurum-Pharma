import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  FormError,
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
  usePlatformPaymentSubmissionDetail,
  useRejectPlatformPaymentSubmission,
  useReviewPlatformPaymentSubmission,
} from "./queries";
import {
  type PlatformBankPaymentReview,
  type PlatformPaymentSubmissionListItem,
} from "./types";

const PRIMARY_TJS_ACCOUNT_KEY = "aurum_tjs_primary";

const reviewSchema = z.object({
  recipient_account_key: z.literal(PRIMARY_TJS_ACCOUNT_KEY),
  confirmed: z.boolean().refine((value) => value, "Подтвердите сверку с банковской системой"),
});

const rejectionSchema = z
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

type ReviewForm = z.infer<typeof reviewSchema>;
type RejectionForm = z.infer<typeof rejectionSchema>;

export function PaymentSubmissionReviewModal({
  item,
  online,
  onClose,
  onReviewed,
  onRejected,
  onRefreshRequired,
}: {
  item: PlatformPaymentSubmissionListItem | null;
  online: boolean;
  onClose: () => void;
  onReviewed: (review: PlatformBankPaymentReview) => void;
  onRejected: () => void;
  onRefreshRequired: (message: string) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const tenantId = item?.tenant_id ?? "";
  const submissionId = item?.submission_id ?? "";
  const detailQuery = usePlatformPaymentSubmissionDetail(
    tenantId,
    submissionId,
    item !== null,
  );
  const reviewMutation = useReviewPlatformPaymentSubmission();
  const rejectionMutation = useRejectPlatformPaymentSubmission();
  const [mode, setMode] = useState<"review" | "reject">("review");
  const [reviewOperationId, setReviewOperationId] = useState(createOperationId);
  const [rejectionOperationId, setRejectionOperationId] = useState(createOperationId);
  const [topError, setTopError] = useState<string | null>(null);
  const reviewForm = useForm<ReviewForm>({
    defaultValues: { recipient_account_key: PRIMARY_TJS_ACCOUNT_KEY, confirmed: false },
  });
  const rejectionForm = useForm<RejectionForm>({
    defaultValues: { reason_code: "bank_payment_not_found", reason_note: "" },
  });
  const pending = reviewMutation.isPending || rejectionMutation.isPending;

  useEffect(() => {
    if (!item) return;
    setMode("review");
    setTopError(null);
    setReviewOperationId(createOperationId());
    setRejectionOperationId(createOperationId());
    reviewForm.reset({ recipient_account_key: PRIMARY_TJS_ACCOUNT_KEY, confirmed: false });
    rejectionForm.reset({ reason_code: "bank_payment_not_found", reason_note: "" });
  }, [item, rejectionForm, reviewForm]);

  const clearSensitiveDetail = useCallback(() => {
    if (!tenantId || !submissionId) return;
    queryClient.removeQueries({
      queryKey: platformBillingKeys.submissionDetail(tenantId, submissionId),
    });
  }, [queryClient, submissionId, tenantId]);

  useEffect(() => clearSensitiveDetail, [clearSensitiveDetail]);

  useEffect(() => {
    if (!item || !detailQuery.data) return;
    const hideSensitiveDetail = () => {
      clearSensitiveDetail();
      onClose();
      onRefreshRequired("Защищённые реквизиты скрыты. Откройте заявку повторно.");
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

  const close = () => {
    if (pending) return;
    clearSensitiveDetail();
    setTopError(null);
    onClose();
  };

  const review = reviewForm.handleSubmit(async (values) => {
    const detail = detailQuery.data;
    if (!detail) return;
    const parsed = reviewSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "confirmed" || field === "recipient_account_key") {
          reviewForm.setError(field, { message: issue.message });
        }
      }
      return;
    }
    if (!online) {
      setTopError("Нет подключения. Передача заявки временно недоступна.");
      return;
    }

    setTopError(null);
    try {
      const result = await reviewMutation.mutateAsync({
        tenantId: detail.tenant_id,
        submissionId: detail.submission_id,
        payload: {
          operation_id: reviewOperationId,
          expected_row_version: detail.row_version,
          recipient_account_key: parsed.data.recipient_account_key,
        },
      });
      clearSensitiveDetail();
      onReviewed(result.item);
    } catch (error) {
      handleConflictOrError(error, "Не удалось передать заявку на подтверждение.");
    }
  });

  const reject = rejectionForm.handleSubmit(async (values) => {
    const detail = detailQuery.data;
    if (!detail) return;
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
    if (!online) {
      setTopError("Нет подключения. Отклонение заявки временно недоступно.");
      return;
    }

    setTopError(null);
    try {
      await rejectionMutation.mutateAsync({
        tenantId: detail.tenant_id,
        submissionId: detail.submission_id,
        payload: {
          operation_id: rejectionOperationId,
          expected_row_version: detail.row_version,
          reason_code: parsed.data.reason_code,
          reason_note: parsed.data.reason_note || null,
        },
      });
      clearSensitiveDetail();
      onRejected();
    } catch (error) {
      handleConflictOrError(error, "Не удалось отклонить заявку.");
    }
  });

  const handleConflictOrError = (error: unknown, fallback: string) => {
    if (isAxiosError(error) && error.response?.status === 409) {
      clearSensitiveDetail();
      onClose();
      onRefreshRequired("Другой сотрудник уже обработал заявку. Очередь обновлена.");
      return;
    }
    setTopError(describeApiError(error, fallback));
  };

  return (
    <Modal
      open={item !== null}
      onClose={close}
      title={mode === "reject" ? "Отклонение заявки" : "Проверка заявки об оплате"}
      className="max-w-2xl"
    >
      {detailQuery.isLoading ? (
        <SkeletonRows rows={5} />
      ) : detailQuery.error && !detailQuery.data ? (
        <div className="space-y-4" role="alert">
          <p className="text-sm text-danger-foreground">
            {describeApiError(detailQuery.error, "Не удалось открыть защищённые данные заявки")}
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
      ) : detailQuery.data ? (
        mode === "reject" ? (
          <form className="space-y-4" noValidate onSubmit={reject}>
            <SubmissionSummary detail={detailQuery.data} />
            {!online ? <OfflineSubmissionNotice /> : null}
            <div>
              <Label htmlFor="submission-rejection-reason">Причина</Label>
              <Select
                id="submission-rejection-reason"
                autoFocus
                disabled={pending}
                {...rejectionForm.register("reason_code")}
              >
                <option value="bank_payment_not_found">Платёж не найден в банке</option>
                <option value="amount_mismatch">Сумма не совпадает</option>
                <option value="date_mismatch">Дата не совпадает</option>
                <option value="duplicate">Повторная заявка</option>
                <option value="wrong_tenant_or_invoice">Другая аптека или счёт</option>
                <option value="other">Другая причина</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="submission-rejection-note">Комментарий для клиента</Label>
              <Textarea
                id="submission-rejection-note"
                rows={3}
                maxLength={500}
                disabled={pending}
                invalid={Boolean(rejectionForm.formState.errors.reason_note)}
                {...rejectionForm.register("reason_note")}
              />
              <FormError>{rejectionForm.formState.errors.reason_note?.message}</FormError>
            </div>
            <CommandError error={topError} />
            <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
              <Button
                type="button"
                variant="secondary"
                disabled={pending}
                onClick={() => {
                  setMode("review");
                  setTopError(null);
                }}
              >
                Назад
              </Button>
              <Button type="submit" variant="danger" disabled={!online} isLoading={pending}>
                Отклонить заявку
              </Button>
            </div>
          </form>
        ) : (
          <form className="space-y-4" noValidate onSubmit={review}>
            <SubmissionSummary detail={detailQuery.data} />
            {!online ? <OfflineSubmissionNotice /> : null}
            <input type="hidden" {...reviewForm.register("recipient_account_key")} />
            <p className="rounded-lg border border-info/25 bg-info-subtle px-3 py-2 text-sm text-info-foreground">
              После сверки заявка попадёт другому сотруднику на независимое подтверждение. Деньги
              пока не будут зачислены.
            </p>
            <label className="flex min-h-11 items-start gap-3 rounded-lg border border-border px-3 py-2.5 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 h-5 w-5"
                disabled={pending}
                {...reviewForm.register("confirmed")}
              />
              <span>Я сверил номер операции, сумму и дату с банковской системой.</span>
            </label>
            <FormError>{reviewForm.formState.errors.confirmed?.message}</FormError>
            <CommandError error={topError} />
            <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
              <Button type="button" variant="secondary" disabled={pending} onClick={close}>
                Закрыть
              </Button>
              <Button
                type="button"
                variant="danger"
                disabled={!online || pending}
                onClick={() => {
                  setMode("reject");
                  setTopError(null);
                }}
              >
                Отклонить
              </Button>
              <Button type="submit" disabled={!online} isLoading={reviewMutation.isPending}>
                Передать на подтверждение
              </Button>
            </div>
          </form>
        )
      ) : null}
    </Modal>
  );
}

function OfflineSubmissionNotice(): JSX.Element {
  return (
    <p className="text-sm text-warning-foreground" role="status">
      Нет подключения. Обработка заявки временно недоступна.
    </p>
  );
}

function SubmissionSummary({
  detail,
}: {
  detail: {
    tenant_name: string;
    invoice_number: string;
    amount: string;
    currency: "TJS";
    paid_at: string;
    external_reference: string;
  };
}): JSX.Element {
  return (
    <dl className="grid gap-3 rounded-lg border border-border bg-surface-subtle p-4 sm:grid-cols-2">
      <Summary label="Аптека" value={detail.tenant_name} />
      <Summary label="Счёт" value={detail.invoice_number} />
      <Summary label="Сумма" value={formatBillingMoney(detail.amount, detail.currency)} />
      <Summary label="Дата оплаты" value={formatBillingDate(detail.paid_at)} />
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
    <div className="min-w-0">
      <dt className="text-xs text-foreground-muted">{label}</dt>
      <dd className="mt-1 break-words font-medium text-foreground">{value}</dd>
    </div>
  );
}

function CommandError({ error }: { error: string | null }): JSX.Element | null {
  return error ? (
    <p className="text-sm text-danger" role="alert">
      {error}
    </p>
  ) : null;
}
