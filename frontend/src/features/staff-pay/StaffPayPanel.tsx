import { useState } from "react";

import { Button, Card, CardContent, CardHeader, CardTitle, Pagination } from "@/components/ui";

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
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle>Зарплата и выдачи наличными</CardTitle>
        <div className="flex flex-wrap gap-2">
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
              disabled={query.isFetching}
              onClick={() => void query.refetch()}
            >
              {query.isFetching ? "Обновление…" : "Обновить сведения"}
            </Button>
          ) : null}
          {own && visible ? (
            <Button
              variant="secondary"
              onClick={() => {
                setVisible(false);
                setHistory(null);
              }}
            >
              Скрыть сведения
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-foreground-muted">
          Руководитель вручную указывает сумму за период и уже выданные наличные. Авансы входят в
          общую выданную сумму.
        </p>
        {!visible ? (
          <Button variant="secondary" onClick={() => setVisible(true)}>
            Показать сведения
          </Button>
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
              <ul className="grid gap-4 md:grid-cols-2" aria-label="Периоды зарплаты">
                {query.data.items.map((period) => (
                  <li key={period.id}>
                    <PeriodCard
                      period={period}
                      timezone={query.data.timezone}
                      manage={canManage}
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
  onIntent,
  onHistory,
}: {
  period: StaffPayPeriod;
  timezone: string;
  manage: boolean;
  onIntent: (intent: PayIntent) => void;
  onHistory: (mode: "entries" | "revisions") => void;
}): JSX.Element {
  const overpaid = period.remaining_amount.startsWith("-");
  return (
    <article className="h-full min-w-0 space-y-4 rounded-lg border border-border p-4">
      <h3 className="font-medium">
        {payDate(period.period_from)} — {payDate(period.period_to)}
      </h3>
      <dl className="space-y-3 text-sm">
        <Total label="Сумма за период" value={period.amount} />
        <Total label="Выдано наличными" value={period.paid_amount} />
        <Total label="Из них авансы" value={period.advance_amount} />
        <Total
          label={overpaid ? "Выдано сверх суммы" : "Осталось выдать"}
          value={overpaid ? period.remaining_amount.slice(1) : period.remaining_amount}
        />
      </dl>
      {period.note ? <p className="break-words text-sm">{period.note}</p> : null}
      <p className="text-xs text-foreground-muted">
        Обновлено: {payDateTime(period.updated_at, timezone)}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => onHistory("entries")}>
          История выдач
        </Button>
        <Button variant="secondary" onClick={() => onHistory("revisions")}>
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

function Total({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex flex-wrap justify-between gap-2">
      <dt className="text-foreground-muted">{label}</dt>
      <dd className="font-medium">{payMoney(value)}</dd>
    </div>
  );
}
