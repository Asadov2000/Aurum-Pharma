import {
  Badge,
  Button,
  Card,
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
import { formatBillingDate, formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";

import { usePlatformPaymentSubmissions } from "./queries";
import {
  type PlatformPaymentSubmissionListItem,
  type PlatformPaymentSubmissionStatus,
} from "./types";

const PAGE_SIZE = 20;

export function PaymentSubmissionQueue({
  query,
  page,
  onPage,
  online,
  onReview,
}: {
  query: ReturnType<typeof usePlatformPaymentSubmissions>;
  page: number;
  onPage: (page: number) => void;
  online: boolean;
  onReview: (item: PlatformPaymentSubmissionListItem) => void;
}): JSX.Element {
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h3 className="font-semibold text-foreground">Заявки клиентов</h3>
          <p className="mt-1 text-xs text-foreground-muted">
            Банковские оплаты, которые аптеки самостоятельно отправили на проверку.
          </p>
        </div>
        {query.data ? <Badge tone="neutral">{query.data.total}</Badge> : null}
      </div>

      {query.isLoading ? (
        <div className="p-4">
          <SkeletonRows rows={4} />
        </div>
      ) : query.error && !query.data ? (
        <div className="p-4">
          <div
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2"
            role="alert"
          >
            <p className="text-sm text-danger-foreground">
              {describeApiError(query.error, "Не удалось загрузить заявки клиентов")}
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
        </div>
      ) : query.data?.items.length === 0 ? (
        <div className="p-4">
          <TableEmpty title="Новых заявок нет">
            Когда аптека сообщит о банковской оплате, заявка появится здесь.
          </TableEmpty>
        </div>
      ) : query.data ? (
        <>
          <div className="hidden overflow-x-auto md:block">
            <Table aria-label="Заявки клиентов об оплате">
              <THead>
                <TR>
                  <TH>Счёт</TH>
                  <TH>Дата оплаты</TH>
                  <TH>Операция</TH>
                  <TH className="text-right">Сумма</TH>
                  <TH>Статус</TH>
                  <TH>
                    <span className="sr-only">Действие</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {query.data.items.map((item) => (
                  <TR key={item.submission_id}>
                    <TD className="whitespace-nowrap font-medium text-primary">
                      {item.invoice_number}
                    </TD>
                    <TD className="whitespace-nowrap">{formatBillingDate(item.paid_at)}</TD>
                    <TD className="whitespace-nowrap font-mono text-xs">
                      {maskedReference(item.reference_suffix)}
                    </TD>
                    <TD className="whitespace-nowrap text-right font-semibold tabular-nums">
                      {formatBillingMoney(item.amount, item.currency)}
                    </TD>
                    <TD>
                      <Badge tone={statusTone(item.status)}>{statusLabel[item.status]}</Badge>
                    </TD>
                    <TD className="text-right">
                      {item.status === "submitted" ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={!online}
                          onClick={() => onReview(item)}
                        >
                          Проверить
                        </Button>
                      ) : null}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>

          <ul className="divide-y divide-border md:hidden" aria-label="Заявки клиентов об оплате">
            {query.data.items.map((item) => (
              <li key={item.submission_id} className="space-y-3 px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-all text-sm font-semibold text-primary">
                      {item.invoice_number}
                    </p>
                    <p className="mt-1 text-xs text-foreground-muted">
                      {formatBillingDate(item.paid_at)} · {maskedReference(item.reference_suffix)}
                    </p>
                  </div>
                  <Badge tone={statusTone(item.status)}>{statusLabel[item.status]}</Badge>
                </div>
                <div className="flex items-end justify-between gap-3 border-t border-border pt-3">
                  <p className="font-semibold tabular-nums text-foreground">
                    {formatBillingMoney(item.amount, item.currency)}
                  </p>
                  {item.status === "submitted" ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={!online}
                      onClick={() => onReview(item)}
                    >
                      Проверить
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>

          {query.data.total > PAGE_SIZE ? (
            <div className="border-t border-border p-3">
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={query.data.total}
                onPage={onPage}
              />
            </div>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

const statusLabel: Record<PlatformPaymentSubmissionStatus, string> = {
  submitted: "Новая",
  under_review: "Передана",
  approved: "Подтверждена",
  rejected: "Отклонена",
  duplicate: "Повторная",
  withdrawn: "Отозвана",
};

function statusTone(status: PlatformPaymentSubmissionStatus) {
  if (status === "approved") return "success" as const;
  if (status === "rejected" || status === "duplicate") return "danger" as const;
  if (status === "submitted" || status === "under_review") return "info" as const;
  return "neutral" as const;
}

function maskedReference(suffix: string): string {
  return suffix ? `•••• ${suffix}` : "Скрыт";
}
