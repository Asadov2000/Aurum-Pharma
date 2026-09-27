import { useState } from "react";
import { Link } from "@tanstack/react-router";

import { NavIcon } from "@/components/layout/icons";
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
import { PersonalStaffPayPanel } from "@/features/staff-pay/StaffPayPanel";

import { usePersonalShiftsQuery } from "./queries";
import { type PersonalShift } from "./types";

export function MyWorkPage(): JSX.Element {
  const [page, setPage] = useState(1);
  const shifts = usePersonalShiftsQuery(page);
  return (
    <div className="mx-auto w-full max-w-6xl space-y-5">
      <PageHeader
        title="Моя работа"
        showTitleOnDesktop
        description="Ваши кассовые смены и сведения о наличных выплатах."
        actions={
          <Link
            to="/profile"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-border bg-surface px-4 py-2 text-sm font-medium text-foreground hover:bg-foreground/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          >
            <NavIcon to="/profile" />
            Мой профиль
          </Link>
        }
      />
      <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Card className="min-w-0">
          <CardHeader className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2">
              <span className="text-primary">
                <NavIcon to="/my-work" />
              </span>
              Мои кассовые смены
            </CardTitle>
            <Badge tone="neutral">Только мои</Badge>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-foreground-muted">
              Смены, которые вы открывали. Время указано по часовому поясу аптеки.
            </p>
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-primary/[0.05] p-3">
              <p className="min-w-0 break-words text-sm text-foreground-muted" aria-live="polite">
                {!shifts.isPending && !shifts.isError && shifts.data ? (
                  <>
                    Данные на{" "}
                    {formatTime(new Date(shifts.dataUpdatedAt).toISOString(), shifts.data.timezone)}{" "}
                    · время аптеки ({formatZone(shifts.data.timezone)})
                  </>
                ) : shifts.isError ? (
                  "Данные не обновлены"
                ) : (
                  "Загрузка смен…"
                )}
              </p>
              <Button
                variant="secondary"
                disabled={shifts.isFetching}
                onClick={() => void shifts.refetch()}
              >
                {shifts.isFetching ? "Обновление…" : "Обновить"}
              </Button>
            </div>
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
                {shifts.data.items.length === 0 ? (
                  <p className="rounded-md bg-foreground/[0.03] p-4 text-sm text-foreground-secondary">
                    {shifts.data.total === 0
                      ? "У вас пока нет кассовых смен. Открытые вами смены появятся здесь."
                      : "На этой странице смен нет. Вернитесь на предыдущую страницу."}
                  </p>
                ) : (
                  <ul className="space-y-4" aria-label="Ваши смены">
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
            <p className="rounded-lg bg-foreground/[0.03] p-3 text-sm text-foreground-muted">
              Время открытия кассы — не табель рабочего времени. Продажи и возвраты относятся ко
              всей смене.
            </p>
          </CardContent>
        </Card>
        <div className="min-w-0 space-y-5">
          <PersonalStaffPayPanel />
          <aside
            className="rounded-xl border border-border bg-primary/[0.04] p-5"
            aria-labelledby="work-pay-help"
          >
            <h2 id="work-pay-help" className="font-medium text-foreground">
              Нужна проверка сведений?
            </h2>
            <p className="mt-2 text-sm text-foreground-muted">
              Если период или выдача не совпадают с вашими договорённостями, уточните их у
              руководителя.
            </p>
            <details className="mt-3 text-sm">
              <summary className="min-h-11 cursor-pointer py-3 font-medium text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                Как читать выплаты
              </summary>
              <p className="mt-2 text-foreground-secondary">
                Сумму за период руководитель указывает вручную. Выданные наличные включают авансы.
                Остаток — разность суммы периода и выдач. Превышение суммы не создаёт долг
                сотрудника. Продажи и длительность смен не рассчитывают зарплату.
              </p>
            </details>
          </aside>
        </div>
      </div>
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
    <article
      className={`min-w-0 space-y-4 rounded-xl border p-4 sm:p-5 ${shift.status === "open" ? "border-primary/30 bg-primary/[0.04]" : "border-border"}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="mb-2 font-semibold text-foreground">
            {new Intl.DateTimeFormat("ru-RU", { dateStyle: "long", timeZone: timezone }).format(
              new Date(shift.opened_at),
            )}
          </p>
          <h3 className="break-words font-medium text-foreground">{shift.branch_name}</h3>
          <p className="mt-1 break-words text-sm text-foreground-secondary">
            {shift.register_name}
          </p>
        </div>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-5 text-sm">
        <ShiftDetail label="Открытие" value={formatTime(shift.opened_at, timezone)} />
        <ShiftDetail
          label="Закрытие"
          value={shift.closed_at ? formatTime(shift.closed_at, timezone) : "Ещё не закрыта"}
        />
        <ShiftDetail label="Продажи" value={formatMoney(shift.sales_total, shift.currency)} />
        <ShiftDetail label="Возвраты" value={formatMoney(shift.returns_total, shift.currency)} />
      </dl>
      <div className="border-t border-border pt-3 text-sm text-foreground-muted">
        {shift.closed_at ? (
          <p>
            Касса была открыта{" "}
            <span className="font-medium">{shiftDuration(shift.opened_at, shift.closed_at)}</span>
          </p>
        ) : (
          <p>
            {shift.status === "open"
              ? "Промежуточные итоги открытой смены"
              : "Итоги приостановленной смены"}
          </p>
        )}
      </div>
    </article>
  );
}

function ShiftDetail({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="min-w-0 space-y-1">
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

function formatZone(timezone: string): string {
  return (
    new Intl.DateTimeFormat("ru-RU", { timeZone: timezone, timeZoneName: "short" })
      .formatToParts(new Date())
      .find((part) => part.type === "timeZoneName")?.value ?? timezone
  );
}

function shiftDuration(openedAt: string, closedAt: string): string {
  const minutes = Math.max(0, Math.floor((Date.parse(closedAt) - Date.parse(openedAt)) / 60_000));
  if (minutes === 0) return "Менее минуты";
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `${hours} ч ${minutes % 60} мин` : `${minutes} мин`;
}
