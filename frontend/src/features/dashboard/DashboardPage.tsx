import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import { Badge, Button, PageHeader, SkeletonRows } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { useDashboardSummary } from "./queries";
import {
  type DashboardSummary,
  type ExpiringBatch,
  type ExpiringLicense,
  type ExpiryStatus,
  type FinanceSection,
  type TodaySection,
} from "./types";

const expiryTone: Record<ExpiryStatus, "neutral" | "success" | "warning" | "danger" | "info"> = {
  expired: "danger",
  red: "danger",
  orange: "warning",
  yellow: "warning",
  normal: "neutral",
};

const subscriptionLabel: Record<string, string> = {
  trial: "Пробный период",
  active: "Активна",
  grace_period: "Льготный период",
  suspended: "Приостановлена",
  cancelled: "Отменена",
  archived: "Архив",
};

function subscriptionTone(status: string): "success" | "warning" | "danger" | "info" {
  if (status === "active") return "success";
  if (status === "trial") return "info";
  if (status === "grace_period") return "warning";
  return "danger";
}

const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const countFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });

function money(value: string | number, currency: string): string {
  return `${moneyFormatter.format(Number(value))} ${currency}`;
}

function count(value: string | number): string {
  return countFormatter.format(Number(value));
}

export function DashboardPage(): JSX.Element {
  const { user } = useAuth();
  const permissions = user?.permissions ?? [];
  const hasAccess = (code: string): boolean =>
    (user?.is_developer === true && !user.support_access) || permissions.includes(code);
  const hasTenant = Boolean(user?.home_tenant_id);
  const canView =
    Boolean(user?.is_developer || user?.is_administrator) || permissions.includes("reports.view");
  const query = useDashboardSummary(hasTenant && canView);

  if (hasTenant && !canView) {
    return (
      <AccessDeniedCard
        title="Главная"
        message="Сводка по аптеке доступна владельцу и администратору."
      />
    );
  }

  if (!hasTenant) {
    return <SupportProfile />;
  }

  const canOpenPos = hasAccess("pos.sell");
  const canViewIncoming = hasAccess("incoming.view");
  const canCreateIncoming = canViewIncoming && hasAccess("incoming.create");
  const canOpenCatalog = hasAccess("catalog.view");
  const canOpenBatches = hasAccess("batches.view");
  const canOpenBranches = hasAccess("branches.view");
  const canOpenBilling = ["billing.overview.view", "billing.invoice.view"].every(hasAccess);
  const generatedAt = query.data ? formatGeneratedAt(query.data.generated_at) : null;
  const dashboardError = query.error;
  const refreshing = query.isFetching;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Главная"
        description="Сводка по аптеке за сегодня"
        meta={generatedAt ? `Обновлено ${generatedAt}` : undefined}
        showTitleOnDesktop
        actions={
          <>
            {canOpenPos && (
              <HeaderActionLink to="/pos" label="Открыть кассу" icon={<RegisterIcon />} primary />
            )}
            {canCreateIncoming && (
              <HeaderActionLink to="/incoming" label="Принять поставку" icon={<IncomingIcon />} />
            )}
            <Button
              variant="secondary"
              size="lg"
              className="w-[var(--control-height-lg)] px-0"
              aria-label="Обновить сводку"
              isLoading={refreshing}
              onClick={() => query.refresh()}
            >
              <SyncIcon />
            </Button>
          </>
        }
      />

      {dashboardError && (
        <div
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm text-danger-foreground"
          role="alert"
        >
          <p>{describeApiError(dashboardError, "Не удалось обновить сводку")}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            isLoading={refreshing}
            onClick={() => (query.data ? query.refresh() : void query.refetch())}
          >
            Повторить
          </Button>
        </div>
      )}

      {query.isLoading ? (
        <SkeletonRows rows={6} />
      ) : query.data ? (
        <DashboardWorkspace
          data={query.data}
          canOpenPos={canOpenPos}
          canViewIncoming={canViewIncoming}
          canCreateIncoming={canCreateIncoming}
          canOpenCatalog={canOpenCatalog}
          canOpenBatches={canOpenBatches}
          canOpenBranches={canOpenBranches}
          canOpenBilling={canOpenBilling}
        />
      ) : null}

      {query.data && (
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-1 pt-3 text-xs text-foreground-muted">
          <span>Данные обновляются раз в минуту</span>
          <span className={dashboardError ? "text-danger" : "text-success-foreground"}>
            {dashboardError ? "Последнее обновление не выполнено" : "Синхронизация выполнена"}
          </span>
        </footer>
      )}
    </div>
  );
}

function HeaderActionLink({
  to,
  label,
  icon,
  primary = false,
}: {
  to: "/pos" | "/incoming";
  label: string;
  icon: ReactNode;
  primary?: boolean;
}): JSX.Element {
  return (
    <Link
      to={to}
      className={cn(
        "inline-flex h-[var(--control-height-lg)] shrink-0 items-center justify-center gap-2 rounded-md px-[var(--control-padding-lg)] text-base font-semibold transition-colors duration-fast",
        primary
          ? "bg-primary text-primary-foreground shadow-sm hover:bg-primary/90"
          : "border border-input bg-surface text-foreground shadow-sm hover:border-foreground/25 hover:bg-foreground/[0.025]",
      )}
    >
      {icon}
      {label}
    </Link>
  );
}

function DashboardWorkspace({
  data,
  canOpenPos,
  canViewIncoming,
  canCreateIncoming,
  canOpenCatalog,
  canOpenBatches,
  canOpenBranches,
  canOpenBilling,
}: {
  data: DashboardSummary;
  canOpenPos: boolean;
  canViewIncoming: boolean;
  canCreateIncoming: boolean;
  canOpenCatalog: boolean;
  canOpenBatches: boolean;
  canOpenBranches: boolean;
  canOpenBilling: boolean;
}): JSX.Element {
  return (
    <>
      <TodaySummary data={data.today} />
      <div className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
        <AttentionPanel
          batches={data.expiring.batches}
          licenses={data.expiring.licenses}
          canOpenBatches={canOpenBatches}
          canOpenBranches={canOpenBranches}
        />
        <ChecklistPanel
          draftIncoming={data.checklist.draft_incoming_count}
          closedShifts={data.checklist.closed_shifts_count}
          latestClosedShiftId={data.checklist.latest_closed_shift_id}
          canViewIncoming={canViewIncoming}
        />
        <FinancePanel data={data.finance} canOpenBilling={canOpenBilling} />
        <QuickActions
          canOpenPos={canOpenPos}
          canCreateIncoming={canCreateIncoming}
          canOpenCatalog={canOpenCatalog}
        />
      </div>
    </>
  );
}

function TodaySummary({ data }: { data: TodaySection }): JSX.Element {
  const metrics = [
    {
      label: "Продажи до возвратов",
      value: money(data.revenue, data.currency),
      icon: <RegisterIcon />,
    },
    { label: "Чеков продаж", value: count(data.receipts), icon: <ReceiptIcon /> },
    { label: "Активных смен", value: count(data.active_shifts), icon: <ShiftIcon /> },
    { label: "Кассиров на смене", value: count(data.cashiers_on_shift), icon: <ShiftIcon /> },
  ];

  return (
    <section
      aria-label="Показатели за сегодня"
      className="grid grid-cols-2 overflow-hidden rounded-lg border border-border bg-surface xl:grid-cols-4"
    >
      <h2 className="sr-only">Сегодня</h2>
      {metrics.map((metric, index) => (
        <div
          key={metric.label}
          className={cn(
            "flex min-w-0 items-center gap-3 border-border px-4 py-4 xl:border-b-0",
            index < 2 && "border-b",
            index % 2 === 0 && "border-r",
            index < metrics.length - 1 && "xl:border-r",
          )}
        >
          <span className="grid h-10 w-10 shrink-0 place-items-center text-primary" aria-hidden>
            {metric.icon}
          </span>
          <div className="min-w-0">
            <p className="text-sm text-foreground-secondary">{metric.label}</p>
            <p className="truncate font-mono text-xl font-semibold tabular-nums text-foreground sm:text-2xl">
              {metric.value}
            </p>
          </div>
        </div>
      ))}
    </section>
  );
}

function AttentionPanel({
  batches,
  licenses,
  canOpenBatches,
  canOpenBranches,
}: {
  batches: ExpiringBatch[];
  licenses: ExpiringLicense[];
  canOpenBatches: boolean;
  canOpenBranches: boolean;
}): JSX.Element {
  const [requestedTab, setRequestedTab] = useState<"batches" | "licenses">("batches");
  const activeTab =
    requestedTab === "batches" && batches.length === 0 && licenses.length > 0
      ? "licenses"
      : requestedTab;
  const empty = batches.length === 0 && licenses.length === 0;

  return (
    <Panel title="Сроки годности и лицензии" ariaLabel="Требует внимания">
      <div className="flex min-h-10 items-end gap-1 border-b border-border px-4 sm:px-5">
        <AttentionTab
          label="Партии"
          count={batches.length}
          active={activeTab === "batches"}
          onClick={() => setRequestedTab("batches")}
        />
        <AttentionTab
          label="Лицензии"
          count={licenses.length}
          active={activeTab === "licenses"}
          onClick={() => setRequestedTab("licenses")}
        />
      </div>

      {empty ? (
        <div className="flex min-h-48 items-center justify-center px-5 py-8 text-sm text-foreground-muted">
          Срочных предупреждений нет
        </div>
      ) : activeTab === "batches" ? (
        <BatchAttentionList batches={batches} canOpen={canOpenBatches} />
      ) : (
        <LicenseAttentionList licenses={licenses} canOpen={canOpenBranches} />
      )}

      {((activeTab === "batches" && canOpenBatches) ||
        (activeTab === "licenses" && canOpenBranches)) && (
        <div className="border-t border-border px-4 py-3 sm:px-5">
          <Link
            to={activeTab === "batches" ? "/batches" : "/branches"}
            className="inline-flex min-h-9 items-center gap-2 text-sm font-medium text-primary hover:underline"
          >
            {activeTab === "batches" ? "Открыть партии" : "Открыть торговые точки"}
            <ChevronIcon />
          </Link>
        </div>
      )}
    </Panel>
  );
}

function AttentionTab({
  label,
  count: value,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      className={cn(
        "relative min-h-10 px-3 text-sm font-medium transition-colors duration-fast",
        active ? "text-primary" : "text-foreground-secondary hover:text-foreground",
      )}
      aria-pressed={active}
      onClick={onClick}
    >
      {label} <span className="font-mono">{value}</span>
      {active && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-primary" aria-hidden />}
    </button>
  );
}

function BatchAttentionList({
  batches,
  canOpen,
}: {
  batches: ExpiringBatch[];
  canOpen: boolean;
}): JSX.Element {
  return (
    <div className="divide-y divide-border">
      {batches.slice(0, 4).map((batch) => {
        const content = (
          <>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">
                Партия {batch.batch_number ?? batch.id.slice(0, 8)}
              </p>
              <p className="mt-0.5 truncate text-xs text-foreground-muted">
                Остаток {count(batch.qty_remaining)} · до {formatDate(batch.expires_at)}
              </p>
            </div>
            <Badge tone={expiryTone[batch.expiry_status]}>
              {batch.days_to_expiry <= 0 ? "Просрочена" : `${batch.days_to_expiry} дн.`}
            </Badge>
          </>
        );
        const className =
          "grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 sm:px-5";
        return canOpen ? (
          <Link key={batch.id} to="/batches" className={`${className} hover:bg-foreground/[0.025]`}>
            {content}
          </Link>
        ) : (
          <div key={batch.id} className={className}>
            {content}
          </div>
        );
      })}
    </div>
  );
}

function LicenseAttentionList({
  licenses,
  canOpen,
}: {
  licenses: ExpiringLicense[];
  canOpen: boolean;
}): JSX.Element {
  return (
    <div className="divide-y divide-border">
      {licenses.slice(0, 4).map((license) => {
        const content = (
          <>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{license.branch_name}</p>
              <p className="mt-0.5 text-xs text-foreground-muted">
                Лицензия до {formatDate(license.license_expires_at)}
              </p>
            </div>
            <Badge tone={license.days_left <= 7 ? "danger" : "warning"}>
              {license.days_left <= 0 ? "Истекла" : `${license.days_left} дн.`}
            </Badge>
          </>
        );
        const className =
          "grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 sm:px-5";
        return canOpen ? (
          <Link
            key={license.branch_id}
            to="/branches"
            className={`${className} hover:bg-foreground/[0.025]`}
          >
            {content}
          </Link>
        ) : (
          <div key={license.branch_id} className={className}>
            {content}
          </div>
        );
      })}
    </div>
  );
}

function ChecklistPanel({
  draftIncoming,
  closedShifts,
  latestClosedShiftId,
  canViewIncoming,
}: {
  draftIncoming: number;
  closedShifts: number;
  latestClosedShiftId: string | null;
  canViewIncoming: boolean;
}): JSX.Element {
  const visibleDraftIncoming = canViewIncoming ? draftIncoming : 0;
  const nothing = visibleDraftIncoming === 0 && closedShifts === 0;
  const onReportsClick = () => {
    if (latestClosedShiftId) {
      try {
        window.localStorage.setItem("pos:lastClosedShiftId", latestClosedShiftId);
      } catch {
        // The reports page remains available without the one-time navigation hint.
      }
    }
  };

  return (
    <Panel title="Требует проверки">
      <div className="divide-y divide-border px-4 py-2 sm:px-5">
        {visibleDraftIncoming > 0 && (
          <ChecklistLink
            to="/incoming"
            title="Черновики приёмок"
            description="Проверить и принять на склад"
            value={visibleDraftIncoming}
            icon={<IncomingIcon />}
          />
        )}
        {closedShifts > 0 && (
          <ChecklistLink
            to="/reports"
            title="Итоги закрытых смен за сегодня"
            description="Проверить суммы и расхождения"
            value={closedShifts}
            icon={<ReportIcon />}
            onClick={onReportsClick}
          />
        )}
        {nothing && (
          <div className="flex min-h-36 items-center text-sm text-success-foreground">
            На сегодня обязательных проверок нет
          </div>
        )}
      </div>
      {!nothing && (
        <div className="flex items-center border-t border-border px-4 py-3 text-sm text-success-foreground sm:px-5">
          Остальных обязательных проверок нет
        </div>
      )}
    </Panel>
  );
}

function ChecklistLink({
  to,
  title,
  description,
  value,
  icon,
  onClick,
}: {
  to: "/incoming" | "/reports";
  title: string;
  description: string;
  value: number;
  icon: ReactNode;
  onClick?: () => void;
}): JSX.Element {
  return (
    <Link
      to={to}
      onClick={onClick}
      className="grid min-h-16 grid-cols-[2.5rem_minmax(0,1fr)_auto_auto] items-center gap-3 py-2.5 hover:text-primary"
    >
      <span className="grid h-9 w-9 place-items-center rounded-md bg-background text-primary">
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-foreground">{title}</span>
        <span className="block truncate text-xs text-foreground-muted">{description}</span>
      </span>
      <span className="grid h-8 min-w-8 place-items-center rounded-md border border-border px-2 font-mono text-sm">
        {value}
      </span>
      <ChevronIcon />
    </Link>
  );
}

function FinancePanel({
  data,
  canOpenBilling,
}: {
  data: FinanceSection;
  canOpenBilling: boolean;
}): JSX.Element {
  return (
    <Panel
      title="Финансы"
      action={
        canOpenBilling ? (
          <Link
            to="/billing"
            className="inline-flex min-h-9 items-center rounded-md border border-input px-3 text-sm font-medium text-foreground hover:bg-foreground/[0.025]"
          >
            Тариф и оплата
          </Link>
        ) : undefined
      }
    >
      <div className="grid min-h-40 grid-cols-1 divide-y divide-border px-4 py-3 sm:grid-cols-3 sm:divide-x sm:divide-y-0 sm:px-5">
        <FinanceMetric label="Подписка">
          {data.subscription_status ? (
            <>
              <Badge tone={subscriptionTone(data.subscription_status)}>
                {subscriptionLabel[data.subscription_status] ?? "Неизвестный статус"}
              </Badge>
              {data.subscription_period_end && (
                <p className="mt-2 text-xs text-foreground-muted">
                  до {formatDate(data.subscription_period_end)}
                </p>
              )}
            </>
          ) : (
            <p className="text-sm text-foreground-muted">Нет активной подписки</p>
          )}
        </FinanceMetric>
        <FinanceMetric label="Открытые счета">
          <p className="font-mono text-sm font-semibold text-foreground">
            {data.open_invoices_count} шт. · {money(data.open_invoices_total, data.currency)}
          </p>
        </FinanceMetric>
        <FinanceMetric label="Платежи">
          {data.has_overdue && canOpenBilling ? (
            <Link to="/billing">
              <Badge tone="danger">Есть просрочка — оплатить</Badge>
            </Link>
          ) : data.has_overdue ? (
            <Badge tone="danger">Есть просрочка</Badge>
          ) : (
            <p className="text-sm text-success-foreground">Просроченных платежей нет</p>
          )}
        </FinanceMetric>
      </div>
    </Panel>
  );
}

function FinanceMetric({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return (
    <div className="min-w-0 px-0 py-3 first:pt-0 last:pb-0 sm:px-4 sm:py-2 sm:first:pl-0 sm:last:pr-0">
      <p className="mb-2 text-xs font-medium text-foreground-muted">{label}</p>
      {children}
    </div>
  );
}

function QuickActions({
  canOpenPos,
  canCreateIncoming,
  canOpenCatalog,
}: {
  canOpenPos: boolean;
  canCreateIncoming: boolean;
  canOpenCatalog: boolean;
}): JSX.Element {
  const actions: Array<{
    to: "/pos" | "/incoming" | "/catalog" | "/reports";
    label: string;
    icon: ReactNode;
    available: boolean;
  }> = [
    { to: "/pos", label: "Новая продажа", icon: <RegisterIcon />, available: canOpenPos },
    {
      to: "/incoming",
      label: "Новая приёмка",
      icon: <IncomingIcon />,
      available: canCreateIncoming,
    },
    { to: "/catalog", label: "Каталог", icon: <ReceiptIcon />, available: canOpenCatalog },
    { to: "/reports", label: "Отчёты", icon: <ReportIcon />, available: true },
  ];

  return (
    <Panel title="Быстрые действия">
      <div className="grid grid-cols-1 gap-2 px-4 py-4 sm:grid-cols-2 sm:px-5">
        {actions
          .filter((action) => action.available)
          .map((action) => (
            <Link
              key={action.to}
              to={action.to}
              className="grid min-h-12 grid-cols-[1.75rem_minmax(0,1fr)_auto] items-center gap-2 rounded-md border border-border px-3 text-sm font-medium text-foreground transition-colors duration-fast hover:border-foreground/25 hover:bg-foreground/[0.025]"
            >
              <span className="text-primary">{action.icon}</span>
              <span className="truncate">{action.label}</span>
              <ChevronIcon />
            </Link>
          ))}
      </div>
    </Panel>
  );
}

function Panel({
  title,
  action,
  ariaLabel,
  children,
}: {
  title: string;
  action?: ReactNode;
  ariaLabel?: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section
      aria-label={ariaLabel}
      className="min-w-0 overflow-hidden rounded-lg border border-border bg-surface"
    >
      <header className="flex min-h-14 items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}

function SupportProfile(): JSX.Element {
  const { user } = useAuth();
  return (
    <div className="space-y-4">
      <PageHeader title="Главная" showTitleOnDesktop />
      <section className="max-w-2xl overflow-hidden rounded-lg border border-border bg-surface">
        <header className="border-b border-border px-5 py-4">
          <h2 className="text-lg font-semibold text-foreground">{user?.full_name ?? "Профиль"}</h2>
          <p className="mt-1 text-sm text-foreground-muted">{user?.email}</p>
        </header>
        <div className="px-5 py-4 text-sm text-foreground-secondary">
          <div className="flex flex-wrap gap-2">
            {user?.is_developer && <Badge tone="info">Разработчик</Badge>}
            {user?.is_administrator && <Badge tone="info">Администратор</Badge>}
          </div>
          <p className="mt-3 text-foreground-muted">
            Сводка по аптеке доступна пользователям, привязанным к тенанту. Выберите раздел в меню
            слева.
          </p>
        </div>
      </section>
    </div>
  );
}

function formatGeneratedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ru-RU");
}

function SvgIcon({ children }: { children: ReactNode }): JSX.Element {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      {children}
    </svg>
  );
}

function ReceiptIcon(): JSX.Element {
  return (
    <SvgIcon>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9 8h6M9 12h6" />
    </SvgIcon>
  );
}

function ShiftIcon(): JSX.Element {
  return (
    <SvgIcon>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 20a6 6 0 0 1 12 0M16 7a3 3 0 0 1 0 6M18 20a5 5 0 0 0-3-4.6" />
    </SvgIcon>
  );
}

function RegisterIcon(): JSX.Element {
  return (
    <SvgIcon>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M7 8h.01M7 12h6M7 16h10" />
    </SvgIcon>
  );
}

function IncomingIcon(): JSX.Element {
  return (
    <SvgIcon>
      <path d="M21 16V8l-9-5-9 5v8l9 5 9-5Z" />
      <path d="M12 11v6M9 14l3 3 3-3" />
    </SvgIcon>
  );
}

function ReportIcon(): JSX.Element {
  return (
    <SvgIcon>
      <path d="M4 20h16M7 20v-6M12 20V8M17 20v-9" />
    </SvgIcon>
  );
}

function SyncIcon(): JSX.Element {
  return (
    <SvgIcon>
      <path d="M20 7v5h-5M4 17v-5h5" />
      <path d="M18.5 10A7 7 0 0 0 6 7.5L4 10M5.5 14A7 7 0 0 0 18 16.5l2-2.5" />
    </SvgIcon>
  );
}

function ChevronIcon(): JSX.Element {
  return (
    <SvgIcon>
      <path d="m9 18 6-6-6-6" />
    </SvgIcon>
  );
}
