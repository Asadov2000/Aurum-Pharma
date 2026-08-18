import { useEffect, useState } from "react";
import { isAxiosError } from "axios";

import {
  Badge,
  Button,
  Modal,
  Pagination,
  SkeletonRows,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { formatBillingDate, formatBillingDateTime, formatBillingMoney } from "./format";
import { createOperationId } from "./operationId";
import { usePaymentSubmissionsQuery, useWithdrawPaymentSubmission } from "./queries";
import {
  type TenantPaymentSubmission,
  type TenantPaymentSubmissionStatus,
} from "./types";

const PAGE_SIZE = 10;

export function PaymentSubmissionsPanel({
  canWithdraw,
  online,
  onNotice,
}: {
  canWithdraw: boolean;
  online: boolean;
  onNotice: (message: string) => void;
}): JSX.Element {
  const [page, setPage] = useState(1);
  const [withdrawTarget, setWithdrawTarget] = useState<TenantPaymentSubmission | null>(null);
  const [withdrawOperationId, setWithdrawOperationId] = useState(createOperationId);
  const [withdrawError, setWithdrawError] = useState<string | null>(null);
  const query = usePaymentSubmissionsQuery(page, PAGE_SIZE);
  const mutation = useWithdrawPaymentSubmission();

  useEffect(() => {
    const total = query.data?.total;
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [page, query.data?.total]);

  const openWithdraw = (submission: TenantPaymentSubmission) => {
    setWithdrawTarget(submission);
    setWithdrawOperationId(createOperationId());
    setWithdrawError(null);
  };

  const closeWithdraw = () => {
    if (mutation.isPending) return;
    setWithdrawTarget(null);
    setWithdrawError(null);
  };

  const withdraw = async () => {
    if (!withdrawTarget || !online) return;
    setWithdrawError(null);
    try {
      await mutation.mutateAsync({
        submissionId: withdrawTarget.submission_id,
        payload: {
          operation_id: withdrawOperationId,
          expected_row_version: withdrawTarget.row_version,
        },
      });
      setWithdrawTarget(null);
      onNotice("Заявка на подтверждение оплаты отозвана.");
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        setWithdrawTarget(null);
        onNotice("Заявка уже обрабатывается или была изменена. История обновлена.");
        void query.refetch();
        return;
      }
      setWithdrawError(describeApiError(error, "Не удалось отозвать заявку."));
    }
  };

  return (
    <section className="min-w-0 space-y-3" aria-labelledby="billing-submissions-heading">
      <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id="billing-submissions-heading" className="text-base font-semibold text-foreground">
            Заявки об оплате
          </h2>
          <p className="mt-0.5 text-xs text-foreground-muted">
            Статус банковских платежей, отправленных на проверку Aurum Pharma.
          </p>
        </div>
        {query.data ? <Badge tone="neutral">{submissionCountLabel(query.data.total)}</Badge> : null}
      </div>

      {query.isLoading ? (
        <SkeletonRows rows={4} />
      ) : query.error && !query.data ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2"
          role="alert"
        >
          <p className="text-sm text-danger-foreground">
            {describeApiError(query.error, "Не удалось загрузить заявки об оплате")}
          </p>
          <Button
            variant="secondary"
            size="sm"
            isLoading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : query.data?.items.length === 0 ? (
        <TableEmpty title="Заявок об оплате пока нет">
          После банковского перевода отправьте номер операции, чтобы Aurum Pharma проверил платёж.
        </TableEmpty>
      ) : query.data ? (
        <>
          <SubmissionHistory
            submissions={query.data.items}
            canWithdraw={canWithdraw}
            online={online}
            onWithdraw={openWithdraw}
          />
          {query.data.total > PAGE_SIZE ? (
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={query.data.total}
              onPage={setPage}
            />
          ) : null}
        </>
      ) : null}

      <Modal
        open={withdrawTarget !== null}
        onClose={closeWithdraw}
        title="Отозвать заявку"
      >
        {withdrawTarget ? (
          <div className="space-y-4">
            <p className="text-sm text-foreground-secondary">
              Заявка по счёту <strong>{withdrawTarget.invoice_number}</strong> будет отозвана. Уже
              переданную на независимое подтверждение заявку отозвать нельзя.
            </p>
            {!online ? (
              <p className="text-sm text-warning-foreground" role="status">
                Нет подключения. Действие временно недоступно.
              </p>
            ) : null}
            {withdrawError ? (
              <p className="text-sm text-danger" role="alert">
                {withdrawError}
              </p>
            ) : null}
            <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
              <Button
                variant="secondary"
                disabled={mutation.isPending}
                onClick={closeWithdraw}
              >
                Оставить заявку
              </Button>
              <Button
                variant="danger"
                disabled={!online}
                isLoading={mutation.isPending}
                onClick={() => void withdraw()}
              >
                Отозвать
              </Button>
            </div>
          </div>
        ) : null}
      </Modal>
    </section>
  );
}

function SubmissionHistory({
  submissions,
  canWithdraw,
  online,
  onWithdraw,
}: {
  submissions: readonly TenantPaymentSubmission[];
  canWithdraw: boolean;
  online: boolean;
  onWithdraw: (submission: TenantPaymentSubmission) => void;
}): JSX.Element {
  return (
    <>
      <div className="hidden overflow-x-auto md:block">
        <Table aria-label="Заявки об оплате">
          <THead>
            <TR>
              <TH>Счёт</TH>
              <TH>Оплачено</TH>
              <TH>Отправлено</TH>
              <TH>Операция</TH>
              <TH className="text-right">Сумма</TH>
              <TH>Статус</TH>
              {canWithdraw ? (
                <TH>
                  <span className="sr-only">Действие</span>
                </TH>
              ) : null}
            </TR>
          </THead>
          <TBody>
            {submissions.map((submission) => (
              <TR key={submission.submission_id}>
                <TD className="whitespace-nowrap font-mono text-xs font-semibold text-primary">
                  {submission.invoice_number}
                </TD>
                <TD className="whitespace-nowrap">{formatBillingDate(submission.paid_at)}</TD>
                <TD className="whitespace-nowrap">
                  {formatBillingDateTime(submission.created_at)}
                </TD>
                <TD className="whitespace-nowrap font-mono text-xs">
                  {maskedReference(submission.reference_suffix)}
                </TD>
                <TD className="whitespace-nowrap text-right font-semibold tabular-nums">
                  {formatBillingMoney(submission.amount, submission.currency)}
                </TD>
                <TD>
                  <SubmissionStatus submission={submission} />
                </TD>
                {canWithdraw ? (
                  <TD className="text-right">
                    {submission.can_withdraw && submission.status === "submitted" ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={!online}
                        onClick={() => onWithdraw(submission)}
                      >
                        Отозвать
                      </Button>
                    ) : null}
                  </TD>
                ) : null}
              </TR>
            ))}
          </TBody>
        </Table>
      </div>

      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface md:hidden">
        {submissions.map((submission) => (
          <li key={submission.submission_id} className="space-y-3 px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="break-all font-mono text-sm font-semibold text-primary">
                  {submission.invoice_number}
                </p>
                <p className="mt-1 text-xs text-foreground-muted">
                  {formatBillingDate(submission.paid_at)} · {maskedReference(submission.reference_suffix)}
                </p>
              </div>
              <SubmissionStatus submission={submission} />
            </div>
            <div className="flex items-end justify-between gap-3 border-t border-border pt-3">
              <p className="font-semibold tabular-nums text-foreground">
                {formatBillingMoney(submission.amount, submission.currency)}
              </p>
              {canWithdraw && submission.can_withdraw && submission.status === "submitted" ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!online}
                  onClick={() => onWithdraw(submission)}
                >
                  Отозвать
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function SubmissionStatus({ submission }: { submission: TenantPaymentSubmission }): JSX.Element {
  return (
    <div className="space-y-1">
      <Badge tone={statusTone(submission.status)}>{statusLabel[submission.status]}</Badge>
      {submission.reason_code ? (
        <p className="max-w-52 text-xs text-foreground-muted">
          {reasonLabel[submission.reason_code] ?? "Требуется уточнение у поддержки"}
        </p>
      ) : null}
    </div>
  );
}

const statusLabel: Record<TenantPaymentSubmissionStatus, string> = {
  submitted: "Отправлена",
  under_review: "На проверке",
  approved: "Подтверждена",
  rejected: "Отклонена",
  duplicate: "Повторная",
  withdrawn: "Отозвана",
};

const reasonLabel: Record<string, string> = {
  bank_payment_not_found: "Платёж не найден в банке",
  amount_mismatch: "Сумма не совпадает",
  date_mismatch: "Дата не совпадает",
  duplicate: "Повторная заявка",
  wrong_tenant_or_invoice: "Платёж относится к другой аптеке или счёту",
  other: "Другая причина",
};

function statusTone(status: TenantPaymentSubmissionStatus) {
  if (status === "approved") return "success" as const;
  if (status === "rejected" || status === "duplicate") return "danger" as const;
  if (status === "submitted" || status === "under_review") return "info" as const;
  return "neutral" as const;
}

function maskedReference(suffix: string): string {
  return suffix ? `•••• ${suffix}` : "Скрыт";
}

function submissionCountLabel(count: number): string {
  const absolute = Math.abs(count) % 100;
  const lastDigit = absolute % 10;
  const word =
    absolute > 10 && absolute < 20
      ? "заявок"
      : lastDigit === 1
        ? "заявка"
        : lastDigit >= 2 && lastDigit <= 4
          ? "заявки"
          : "заявок";
  return `${count} ${word}`;
}
