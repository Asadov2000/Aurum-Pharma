import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  PageHeader,
  Pagination,
  SkeletonRows,
} from "@/components/ui";
import { describeApiError } from "@/lib/errorMessages";

import { usePersonalShiftsQuery } from "./queries";
import { type PersonalShift } from "./types";

export function MyWorkPage(): JSX.Element {
  const [page, setPage] = useState(1);
  const shifts = usePersonalShiftsQuery(page);
  return (
    <div className="max-w-5xl space-y-5">
      <PageHeader
        title="Моя работа"
        showTitleOnDesktop
        description="Ваши кассовые смены и итоги работы кассы."
      />
      <Card>
        <CardHeader className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle>Мои кассовые смены</CardTitle>
          <Button
            variant="secondary"
            disabled={shifts.isFetching}
            onClick={() => void shifts.refetch()}
          >
            {shifts.isFetching ? "Обновление…" : "Обновить"}
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-foreground-muted">
            Смены, которые вы открывали. Время указано по часовому поясу аптеки.
          </p>
          {shifts.isPending ? (
            <div>
              <p role="status" className="mb-3 text-sm text-foreground-muted">
                Загрузка смен…
              </p>
              <SkeletonRows rows={3} />
            </div>
          ) : shifts.isError ? (
            <div className="space-y-3">
              <p role="alert" className="text-sm text-danger">
                {describeApiError(shifts.error, "Не удалось загрузить ваши смены.")}
              </p>
              <Button variant="secondary" onClick={() => void shifts.refetch()}>
                Повторить
              </Button>
            </div>
          ) : shifts.data ? (
            <>
              <p className="text-sm text-foreground-muted" aria-live="polite">
                Данные на{" "}
                {formatTime(new Date(shifts.dataUpdatedAt).toISOString(), shifts.data.timezone)}
              </p>
              {shifts.data.items.length === 0 ? (
                <p className="rounded-md bg-foreground/[0.03] p-4 text-sm text-foreground-secondary">
                  {shifts.data.total === 0
                    ? "У вас пока нет кассовых смен. Открытые вами смены появятся здесь."
                    : "На этой странице смен нет. Вернитесь на предыдущую страницу."}
                </p>
              ) : (
                <ul className="grid gap-4 md:grid-cols-2" aria-label="Ваши смены">
                  {shifts.data.items.map((shift) => (
                    <li key={shift.id}>
                      <ShiftCard shift={shift} timezone={shifts.data.timezone} />
                    </li>
                  ))}
                </ul>
              )}
              {shifts.data.total > shifts.data.page_size || page > 1 ? (
                <Pagination
                  page={page}
                  pageSize={shifts.data.page_size}
                  total={shifts.data.total}
                  onPage={setPage}
                />
              ) : null}
            </>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

const shiftStatuses = {
  open: { label: "Открыта", tone: "success" },
  closed: { label: "Закрыта", tone: "neutral" },
  suspended: { label: "Приостановлена", tone: "warning" },
} as const;

function ShiftCard({ shift, timezone }: { shift: PersonalShift; timezone: string }): JSX.Element {
  const status = shiftStatuses[shift.status];
  return (
    <article className="h-full min-w-0 space-y-4 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="break-words font-medium text-foreground">{shift.branch_name}</h3>
          <p className="mt-1 break-words text-sm text-foreground-secondary">
            {shift.register_name}
          </p>
        </div>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <dl className="space-y-3 text-sm">
        <ShiftDetail label="Открытие" value={formatTime(shift.opened_at, timezone)} />
        <ShiftDetail
          label="Закрытие"
          value={shift.closed_at ? formatTime(shift.closed_at, timezone) : "Ещё не закрыта"}
        />
        {shift.closed_at ? (
          <ShiftDetail
            label="Касса была открыта"
            value={shiftDuration(shift.opened_at, shift.closed_at)}
          />
        ) : null}
        <ShiftDetail label="Продажи" value={formatMoney(shift.sales_total, shift.currency)} />
        <ShiftDetail label="Возвраты" value={formatMoney(shift.returns_total, shift.currency)} />
      </dl>
    </article>
  );
}

function ShiftDetail({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex flex-wrap justify-between gap-x-3 gap-y-1">
      <dt className="text-foreground-muted">{label}</dt>
      <dd className="break-words font-medium text-foreground">{value}</dd>
    </div>
  );
}

function formatTime(value: string, timezone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(value));
}

function formatMoney(value: string, currency: string): string {
  return `${new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value))} ${currency}`;
}

function shiftDuration(openedAt: string, closedAt: string): string {
  const minutes = Math.max(0, Math.floor((Date.parse(closedAt) - Date.parse(openedAt)) / 60_000));
  if (minutes === 0) return "Менее минуты";
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `${hours} ч ${minutes % 60} мин` : `${minutes} мин`;
}
