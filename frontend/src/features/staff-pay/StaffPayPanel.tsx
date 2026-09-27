import { useState } from "react";

import { NavIcon } from "@/components/layout/icons";
import { Button, Card, CardContent, CardHeader, CardTitle, Pagination } from "@/components/ui";
import { cn } from "@/lib/utils";

import { payDate, payDateTime, payMoney } from "./format";
import { PayForm, type PayIntent } from "./PayForm";
import { PayHistory, ReadError } from "./PayHistory";
import { useStaffPayAccess, useStaffPayPeriods } from "./queries";
import { type StaffPayPeriod } from "./types";

export function PersonalStaffPayPanel(): JSX.Element {
  const access = useStaffPayAccess();
  return <StaffPayPanel key={access.scope.join(":")} own employeeId={null} />;
}

export function StaffPayPanel({
  own,
  employeeId,
}: {
  own: boolean;
  employeeId: string | null;
}): JSX.Element {
  const [visible, setVisible] = useState(!own);
  const [page, setPage] = useState(1);
  const query = useStaffPayPeriods(own, employeeId, page, visible);
  const access = useStaffPayAccess();
  const [intent, setIntent] = useState<PayIntent | null>(null);
  const [history, setHistory] = useState<{ id: string; mode: "entries" | "revisions" } | null>(
    null,
  );
  const canManage = !own && access.manage;
  const close = () => {
    setIntent(null);
    void query.refetch();
  };
  return (
    <Card className={own ? "rounded-xl" : undefined}>
      <CardHeader
        className={cn(
          "flex flex-wrap items-center justify-between gap-3",
          own && "border-b-0 px-5 pb-0 pt-5 sm:px-6 sm:pt-6",
        )}
      >
        <div>
          <CardTitle className={own ? "text-lg" : undefined}>
            {own ? "Зарплата и выплаты" : "Зарплата и выдачи наличными"}
          </CardTitle>
          {own ? (
            <p className="mt-1 text-xs text-foreground-muted">
              Только ваши сведения · Выдачи наличными
            </p>
          ) : null}
        </div>
        {own ? <LockIcon className="text-primary" /> : null}
        <div
          className={cn(
            "flex flex-wrap gap-2",
            own && (visible ? "w-full justify-between" : "hidden"),
          )}
        >
          {canManage ? (
            <Button
              disabled={!query.data || query.isError}
              onClick={() => setIntent({ kind: "create" })}
            >
              Добавить период
            </Button>
          ) : null}
          {visible ? (
            <Button
              variant="secondary"
              size={own ? "sm" : "md"}
              disabled={query.isFetching}
              onClick={() => void query.refetch()}
            >
              {query.isFetching ? "Обновление…" : "Обновить сведения"}
            </Button>
          ) : null}
          {own && visible ? (
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={visible}
              onClick={() => {
                setVisible(false);
                setHistory(null);
              }}
            >
              <EyeIcon hidden />
              Скрыть сведения
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className={cn("space-y-4", own && "p-5 sm:p-6")}>
        {!own ? (
          <p className="text-sm text-foreground-muted">
            Руководитель вручную указывает сумму за период и уже выданные наличные. Авансы входят в
            общую выданную сумму.
          </p>
        ) : null}
        {!visible ? (
          <div className="flex flex-col items-center rounded-xl border border-dashed border-border bg-foreground/[0.03] px-5 py-7 text-center">
            <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-surface text-primary">
              <LockIcon />
            </span>
            <h3 className="text-sm font-semibold">Сведения скрыты</h3>
            <p className="mb-5 mt-2 max-w-60 text-xs leading-relaxed text-foreground-muted">
              Откройте суммы за свои периоды и историю выдач наличными.
            </p>
            <Button className="w-full" aria-expanded={false} onClick={() => setVisible(true)}>
              <EyeIcon />
              Показать сведения
            </Button>
          </div>
        ) : query.isPending ? (
          <p role="status">Загрузка сведений…</p>
        ) : query.isError ? (
          <ReadError error={query.error} retry={() => void query.refetch()} />
        ) : query.data ? (
          <>
            {query.data.items.length === 0 ? (
              <p className="rounded-md bg-foreground/[0.03] p-4 text-sm">
                {query.data.total === 0
                  ? "Сведений за периоды пока нет."
                  : "На этой странице записей нет."}
              </p>
            ) : (
              <ul
                className={cn(
                  "grid gap-4",
                  own
                    ? "[&>li+li]:border-t [&>li+li]:border-border [&>li+li]:pt-5"
                    : "md:grid-cols-2",
                )}
                aria-label="Периоды зарплаты"
              >
                {query.data.items.map((period) => (
                  <li key={period.id}>
                    <PeriodCard
                      period={period}
                      timezone={query.data.timezone}
                      manage={canManage}
                      personal={own}
                      onIntent={setIntent}
                      onHistory={(mode) => setHistory({ id: period.id, mode })}
                    />
                  </li>
                ))}
              </ul>
            )}
            <Pagination
              page={page}
              pageSize={query.data.page_size}
              total={query.data.total}
              onPage={setPage}
            />
          </>
        ) : null}
        {own ? (
          <p className="text-xs leading-relaxed text-foreground-muted">
            Суммы указывает руководитель вручную. Авансы входят в общую выданную сумму.
            {visible ? " Продажи и длительность кассовых смен не рассчитывают зарплату." : ""}
          </p>
        ) : null}
        {intent && canManage && employeeId && query.data ? (
          <PayForm
            intent={intent}
            employeeId={employeeId}
            timezone={query.data.timezone}
            onClose={close}
          />
        ) : null}
        {history && visible && !query.isError ? (
          <PayHistory
            own={own}
            periodId={history.id}
            mode={history.mode}
            onClose={() => setHistory(null)}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

export function PeriodCard({
  period,
  timezone,
  manage,
  personal = false,
  onIntent,
  onHistory,
}: {
  period: StaffPayPeriod;
  timezone: string;
  manage: boolean;
  personal?: boolean;
  onIntent: (intent: PayIntent) => void;
  onHistory: (mode: "entries" | "revisions") => void;
}): JSX.Element {
  const overpaid = period.remaining_amount.startsWith("-");
  return (
    <article
      className={cn("h-full min-w-0 space-y-4", !personal && "rounded-lg border border-border p-4")}
    >
      {personal ? (
        <div className="border-b border-border pb-4">
          <h3 className="text-sm font-semibold">
            {period.period_from.slice(0, 7) === period.period_to.slice(0, 7)
              ? new Intl.DateTimeFormat("ru-RU", {
                  month: "long",
                  year: "numeric",
                  timeZone: "UTC",
                }).format(new Date(`${period.period_from}T00:00:00Z`))
              : "Период выплат"}
          </h3>
          <p className="mt-1 text-xs text-foreground-muted">
            {payDate(period.period_from)} — {payDate(period.period_to)}
          </p>
        </div>
      ) : (
        <h3 className="font-medium">
          {payDate(period.period_from)} — {payDate(period.period_to)}
        </h3>
      )}
      <dl className="space-y-3 text-sm">
        <Total label="Сумма за период" value={period.amount} personal={personal} />
        <Total label="Выдано наличными" value={period.paid_amount} personal={personal} />
        <Total label="Из них авансы" value={period.advance_amount} personal={personal} advance />
        <Total
          label={overpaid ? "Выдано сверх суммы" : "Осталось выдать"}
          value={overpaid ? period.remaining_amount.slice(1) : period.remaining_amount}
          personal={personal}
          highlighted
        />
      </dl>
      {period.note ? <p className="break-words text-sm">{period.note}</p> : null}
      <p className="text-xs text-foreground-muted">
        Обновлено: {payDateTime(period.updated_at, timezone)}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size={personal ? "sm" : "md"}
          className={personal ? "flex-1 text-xs" : undefined}
          onClick={() => onHistory("entries")}
        >
          {personal ? <NavIcon to="/sales" /> : null}
          История выдач
        </Button>
        <Button
          variant="secondary"
          size={personal ? "sm" : "md"}
          className={personal ? "flex-1 text-xs" : undefined}
          onClick={() => onHistory("revisions")}
        >
          Изменения суммы
        </Button>
        {manage ? (
          <>
            <Button variant="secondary" onClick={() => onIntent({ kind: "edit", period })}>
              Изменить сумму
            </Button>
            <Button onClick={() => onIntent({ kind: "entry", period })}>Записать выдачу</Button>
          </>
        ) : null}
      </div>
    </article>
  );
}

function Total({
  label,
  value,
  personal,
  advance,
  highlighted,
}: {
  label: string;
  value: string;
  personal?: boolean;
  advance?: boolean;
  highlighted?: boolean;
}): JSX.Element {
  return (
    <div
      className={cn(
        "flex flex-wrap justify-between gap-2",
        personal && "items-center",
        personal && "text-xs",
        personal && advance && "pl-3",
        personal && highlighted && "rounded-lg bg-primary/5 p-3.5 text-primary",
      )}
    >
      <dt className={personal && highlighted ? "font-semibold" : "text-foreground-muted"}>
        {label}
      </dt>
      <dd
        className={cn(
          "font-medium",
          personal && "tabular-nums",
          personal && !advance && "text-lg font-semibold",
          personal && advance && "text-foreground-muted",
          personal && highlighted && "text-xl",
        )}
      >
        {payMoney(value)}
      </dd>
    </div>
  );
}

function LockIcon({ className }: { className?: string }): JSX.Element {
  return (
    <svg
      className={cn("h-5 w-5 shrink-0", className)}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3M12 15v2" />
    </svg>
  );
}

function EyeIcon({ hidden = false }: { hidden?: boolean }): JSX.Element {
  return (
    <svg
      className="h-4 w-4 shrink-0"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
      {hidden ? <path d="m3 3 18 18" /> : null}
    </svg>
  );
}
