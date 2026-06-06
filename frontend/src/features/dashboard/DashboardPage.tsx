import { Link } from "@tanstack/react-router";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { useDashboardSummary } from "./queries";
import {
  type ExpiringBatch,
  type ExpiryStatus,
  type FinanceSection,
} from "./types";

const expiryTone: Record<ExpiryStatus, "neutral" | "success" | "warning" | "danger" | "info"> = {
  expired: "danger",
  red: "danger",
  orange: "warning",
  yellow: "warning",
  normal: "neutral",
};

const subscriptionLabel: Record<string, string> = {
  trial: "Пробный",
  active: "Активна",
  grace_period: "Льготный период",
  suspended: "Приостановлена",
  cancelled: "Отменена",
  archived: "Архив",
};

function money(value: string, currency: string): string {
  return `${Number(value).toFixed(2)} ${currency}`;
}

export function DashboardPage(): JSX.Element {
  const { user } = useAuth();
  // Support users (developer/administrator) have no home tenant, so the
  // tenant-scoped summary would 400. Skip the query and show a profile panel.
  const hasTenant = Boolean(user?.home_tenant_id);
  // The summary endpoint needs reports.view (owner/admin/dev). Skip the query
  // for users who'd only get a 403 — e.g. sellers — and show a friendly note.
  const canView =
    Boolean(user?.is_developer || user?.is_administrator) ||
    (user?.permissions ?? []).includes("reports.view");
  const { data, isLoading, error } = useDashboardSummary(hasTenant && canView);

  if (hasTenant && !canView) {
    return (
      <AccessDeniedCard
        title="Главная"
        message="Сводка по аптеке доступна владельцу и администратору."
      />
    );
  }

  if (!hasTenant) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold text-foreground">Главная</h1>
        <Card>
          <CardHeader>
            <CardTitle>{user?.full_name ?? "Профиль"}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm text-foreground-secondary">
            <p>
              <span className="font-medium">Email:</span> {user?.email}
            </p>
            {user?.is_developer && <p className="text-success-foreground">Developer</p>}
            {user?.is_administrator && <p className="text-success-foreground">Administrator</p>}
            <p className="pt-2 text-foreground-muted">
              Сводка по аптеке доступна пользователям, привязанным к тенанту.
              Выберите раздел в меню слева.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Главная</h1>
        {user?.full_name && (
          <span className="text-sm text-foreground-muted">{user.full_name}</span>
        )}
      </div>

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить сводку")}
        </p>
      )}

      {isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : data ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <TodayCard data={data.today} />
          <ExpiringCard
            batches={data.expiring.batches}
            licenses={data.expiring.licenses}
          />
          <FinanceCard data={data.finance} />
          <ChecklistCard
            draftIncoming={data.checklist.draft_incoming_count}
            closedShifts={data.checklist.closed_shifts_count}
            latestClosedShiftId={data.checklist.latest_closed_shift_id}
          />
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------

function TodayCard({ data }: { data: import("./types").TodaySection }): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Сегодня</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Metric label="Выручка" value={money(data.revenue, data.currency)} big />
        <div className="grid grid-cols-3 gap-2">
          <Metric label="Чеков" value={String(data.receipts)} />
          <Metric label="Смен" value={String(data.active_shifts)} />
          <Metric label="Кассиров" value={String(data.cashiers_on_shift)} />
        </div>
      </CardContent>
    </Card>
  );
}

function Metric({
  label,
  value,
  big = false,
}: {
  label: string;
  value: string;
  big?: boolean;
}): JSX.Element {
  return (
    <div>
      <p className="text-xs text-foreground-muted">{label}</p>
      <p
        className={cn(
          "font-mono font-semibold text-foreground",
          big ? "text-2xl" : "text-lg",
        )}
      >
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------

function ExpiringCard({
  batches,
  licenses,
}: {
  batches: ExpiringBatch[];
  licenses: import("./types").ExpiringLicense[];
}): JSX.Element {
  const empty = batches.length === 0 && licenses.length === 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Скоро истекает</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {empty && <p className="text-sm text-foreground-muted">Всё в порядке 👌</p>}

        {batches.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-wide text-foreground-muted">Партии</p>
            <ul className="space-y-1">
              {batches.map((b) => (
                <li key={b.id} className="flex items-center justify-between gap-2 text-sm">
                  <Link
                    to="/batches"
                    className="truncate text-foreground-secondary hover:text-foreground hover:underline"
                  >
                    {b.batch_number ?? b.id.slice(0, 8)}
                  </Link>
                  <Badge tone={expiryTone[b.expiry_status]}>
                    {b.days_to_expiry <= 0
                      ? "просрочена"
                      : `${b.days_to_expiry} дн.`}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        )}

        {licenses.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-wide text-foreground-muted">Лицензии</p>
            <ul className="space-y-1">
              {licenses.map((lic) => (
                <li
                  key={lic.branch_id}
                  className="flex items-center justify-between gap-2 text-sm"
                >
                  <Link
                    to="/branches"
                    className="truncate text-foreground-secondary hover:text-foreground hover:underline"
                  >
                    {lic.branch_name}
                  </Link>
                  <Badge tone={lic.days_left <= 7 ? "danger" : "warning"}>
                    {lic.days_left <= 0 ? "истекла" : `${lic.days_left} дн.`}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------

function FinanceCard({ data }: { data: FinanceSection }): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Финансы</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <p className="text-xs text-foreground-muted">Подписка</p>
          {data.subscription_status ? (
            <div className="flex items-center gap-2">
              <Badge
                tone={data.subscription_status === "active" ? "success" : "info"}
              >
                {subscriptionLabel[data.subscription_status] ?? data.subscription_status}
              </Badge>
              {data.subscription_period_end && (
                <span className="text-xs text-foreground-muted">
                  до {new Date(data.subscription_period_end).toLocaleDateString("ru-RU")}
                </span>
              )}
            </div>
          ) : (
            <p className="text-foreground-muted">нет</p>
          )}
        </div>

        <div>
          <p className="text-xs text-foreground-muted">Открытые счета</p>
          <p className="font-mono">
            {data.open_invoices_count} шт ·{" "}
            {money(data.open_invoices_total, data.currency)}
          </p>
        </div>

        {data.has_overdue && (
          <Link to="/billing">
            <Badge tone="danger">Есть просрочка — оплатить</Badge>
          </Link>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------

function ChecklistCard({
  draftIncoming,
  closedShifts,
  latestClosedShiftId,
}: {
  draftIncoming: number;
  closedShifts: number;
  latestClosedShiftId: string | null;
}): JSX.Element {
  const nothing = draftIncoming === 0 && closedShifts === 0;

  // Pre-fill the shift id so /reports loads the Z-report straight away
  // (ReportsPage reads this localStorage key on mount).
  const onReportsClick = () => {
    if (latestClosedShiftId) {
      window.localStorage.setItem("pos:lastClosedShiftId", latestClosedShiftId);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Чек-лист</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {nothing && <p className="text-foreground-muted">Задач нет 🎉</p>}

        {draftIncoming > 0 && (
          <Link
            to="/incoming"
            className="flex items-center justify-between rounded-md border border-border px-3 py-2 hover:bg-foreground/[0.03]"
          >
            <span>Черновики приходов</span>
            <Badge tone="info">{draftIncoming}</Badge>
          </Link>
        )}

        {closedShifts > 0 && (
          <Link
            to="/reports"
            onClick={onReportsClick}
            className="flex items-center justify-between rounded-md border border-border px-3 py-2 hover:bg-foreground/[0.03]"
          >
            <span>Закрытые смены — Z-отчёт</span>
            <Badge tone="neutral">{closedShifts}</Badge>
          </Link>
        )}
      </CardContent>
    </Card>
  );
}
