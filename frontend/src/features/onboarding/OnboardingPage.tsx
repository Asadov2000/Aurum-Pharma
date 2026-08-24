import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { canAccessPath, getRouteAccessContext } from "@/components/layout/routeAccess";
import { Badge, Button, ConfirmDialog, PageHeader, Skeleton } from "@/components/ui";
import { useMeQuery } from "@/features/auth/queries";
import { activeTenantId } from "@/features/auth/tenantContext";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { readinessStepAction, readinessSteps, taskLabel } from "./labels";
import { useOnboardingOverviewQuery, useStartTrial } from "./queries";
import { type OnboardingOverview, type ReadinessStep, type ReadinessStepCode } from "./types";

const primaryLinkClass =
  "inline-flex h-[var(--control-height-lg)] items-center justify-center gap-2 rounded-md border border-transparent bg-primary px-[var(--control-padding-lg)] text-base font-semibold text-primary-foreground shadow-sm transition-colors duration-fast hover:bg-primary/90 focus-visible:outline-none";

function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(() =>
    typeof navigator === "undefined" ? true : navigator.onLine,
  );

  useEffect(() => {
    const update = (): void => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  return online;
}

function formatDate(value: string | null): string {
  if (!value) return "не указано";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(value));
}

function statusCopy(overview: OnboardingOverview): {
  title: string;
  description: string;
  tone: "neutral" | "success" | "warning";
} {
  if (overview.tenant_status === "setup") {
    return overview.is_ready
      ? {
          title: "Аптека готова к запуску",
          description: "Обязательные настройки проверены. Можно начать пробный период.",
          tone: "success",
        }
      : {
          title: "Подготовьте аптеку к работе",
          description: "Выполните обязательные шаги. Готовность проверяется автоматически.",
          tone: "neutral",
        };
  }
  if (
    !overview.is_ready &&
    (overview.tenant_status === "trial" || overview.tenant_status === "active")
  ) {
    return {
      title: "Завершите обязательную настройку",
      description:
        "Работа уже доступна, но часть важных настроек не завершена. Выполните следующий шаг.",
      tone: "warning",
    };
  }
  if (overview.tenant_status === "trial") {
    return {
      title: "Пробный период активен",
      description: `Доступ ко всем возможностям действует до ${formatDate(overview.trial_ends_at)}.`,
      tone: "success",
    };
  }
  if (overview.tenant_status === "active") {
    return {
      title: "Аптека работает",
      description: "Основная настройка завершена. Текущие рекомендации можно выполнить позже.",
      tone: "success",
    };
  }
  if (overview.tenant_status === "grace_period") {
    return {
      title: "Нужно проверить оплату",
      description: "Доступ временно сохранён. Откройте биллинг и проверьте состояние подписки.",
      tone: "warning",
    };
  }
  if (overview.tenant_status === "readonly") {
    return {
      title: "Доступ ограничен",
      description: "Просмотр данных доступен, но новые операции временно заблокированы.",
      tone: "warning",
    };
  }
  return {
    title: "Организация архивирована",
    description: "Для восстановления работы обратитесь к администратору Aurum Pharma.",
    tone: "warning",
  };
}

function StepValue({ step }: { step: ReadinessStep }): JSX.Element | null {
  if (step.current === null || step.target === null) return null;
  return (
    <span className="whitespace-nowrap text-sm tabular-nums text-foreground-secondary">
      {step.current} из {step.target}
    </span>
  );
}

function OnboardingSkeleton(): JSX.Element {
  return (
    <div className="space-y-4" aria-label="Загрузка готовности">
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-44 w-full" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <Skeleton className="h-[32rem] w-full" />
        <div className="space-y-4">
          <Skeleton className="h-56 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    </div>
  );
}

export function OnboardingPage(): JSX.Element {
  const { data: user } = useMeQuery();
  const tenantId = activeTenantId(user) ?? undefined;
  const access = useMemo(() => getRouteAccessContext(user), [user]);
  const online = useOnlineStatus();
  const overviewQuery = useOnboardingOverviewQuery(tenantId);
  const startTrial = useStartTrial(tenantId);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [operationId, setOperationId] = useState<string | null>(null);

  if (overviewQuery.isPending) return <OnboardingSkeleton />;

  if (overviewQuery.isError || !overviewQuery.data) {
    return (
      <div className="mx-auto max-w-[720px] rounded-lg border border-danger/25 bg-danger/5 p-6">
        <div className="flex items-start gap-3">
          <StatusMark>!</StatusMark>
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-foreground">
              Не удалось проверить готовность
            </h1>
            <p className="mt-1 text-sm leading-5 text-foreground-secondary">
              {online
                ? "Обновите данные. Если ошибка повторится, обратитесь к администратору Aurum Pharma."
                : "Подключитесь к интернету и повторите проверку."}
            </p>
            <Button
              className="mt-4"
              variant="secondary"
              onClick={() => void overviewQuery.refetch()}
              disabled={!online || overviewQuery.isFetching}
              isLoading={overviewQuery.isFetching}
            >
              Проверить снова
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const overview = overviewQuery.data;
  const copy = statusCopy(overview);
  const stepsByCode = new Map(overview.steps.map((step) => [step.code, step]));
  const firstAction =
    readinessSteps
      .map((definition) => {
        const step = stepsByCode.get(definition.code);
        const action = step ? readinessStepAction(definition, step) : null;
        return step?.required && !step.is_complete && action ? action : null;
      })
      .find((action) => action !== null && canAccessPath(action.to, access)) ?? undefined;
  const activeStepCode = readinessSteps.find((definition) => {
    const step = stepsByCode.get(definition.code);
    return step?.required && !step.is_complete;
  })?.code;
  const canOpenPos = canAccessPath("/pos", access);
  const canOpenBilling = canAccessPath("/billing", access);
  const trialCanBeStarted =
    overview.can_start_trial && access.isTenantOwner && overview.tenant_status === "setup";
  const completed = overview.required_completed;
  const total = overview.required_total;
  const progress = total === 0 ? 0 : Math.round((completed / total) * 100);
  const showReadinessProgress =
    !overview.is_ready &&
    (overview.tenant_status === "setup" ||
      overview.tenant_status === "trial" ||
      overview.tenant_status === "active");
  const checkedAt = new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(overviewQuery.dataUpdatedAt));

  const openTrialConfirmation = (): void => {
    setOperationId((current) => current ?? crypto.randomUUID());
    startTrial.reset();
    setConfirmOpen(true);
  };

  const confirmTrial = (): void => {
    if (!operationId) return;
    startTrial.mutate(operationId, {
      onSuccess: () => {
        setConfirmOpen(false);
        setOperationId(null);
      },
    });
  };

  return (
    <div className="space-y-4 pb-4">
      <PageHeader
        title="Старт"
        description="Проверка готовности аптеки к ежедневной работе"
        meta={`Проверено сегодня в ${checkedAt}`}
        showTitleOnDesktop
        actions={
          <Button
            variant="secondary"
            size="lg"
            className="w-[var(--control-height-lg)] px-0"
            aria-label="Проверить готовность снова"
            title="Проверить снова"
            disabled={!online}
            isLoading={overviewQuery.isFetching}
            onClick={() => void overviewQuery.refetch()}
          >
            <RefreshIcon />
          </Button>
        }
      />

      {!online && (
        <div
          className="flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-foreground-secondary"
          role="status"
        >
          <span className="font-semibold text-warning-foreground">Нет сети.</span>
          Показаны последние сохранённые данные. Изменения станут доступны после подключения.
        </div>
      )}

      <LaunchSummary
        overview={overview}
        title={copy.title}
        description={copy.description}
        tone={copy.tone}
        completed={completed}
        total={total}
        progress={progress}
        showProgress={showReadinessProgress}
        firstAction={firstAction}
        canOpenBilling={canOpenBilling}
      />

      <div className="grid min-w-0 grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(19rem,1fr)]">
        <ReadinessPanel stepsByCode={stepsByCode} activeStepCode={activeStepCode} access={access} />

        <aside className="grid min-w-0 gap-4 md:grid-cols-2 xl:grid-cols-1">
          <TrialPanel
            overview={overview}
            canStart={trialCanBeStarted}
            isOwner={access.isTenantOwner}
            online={online}
            onStart={openTrialConfirmation}
          />
          <RecommendedPanel overview={overview} canOpenPos={canOpenPos} />
        </aside>
      </div>

      {startTrial.isError && !confirmOpen && (
        <div
          className="rounded-lg border border-danger/25 bg-danger/5 px-4 py-3 text-sm text-danger"
          role="alert"
        >
          {describeApiError(startTrial.error, "Не удалось начать пробный период")}
        </div>
      )}

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-1 pt-3 text-xs text-foreground-muted">
        <span>Готовность проверяется автоматически</span>
        <span className="inline-flex items-center gap-2 text-success-foreground">
          <SyncIcon />
          Синхронизация выполнена
        </span>
      </footer>

      <ConfirmDialog
        open={confirmOpen}
        title="Начать пробный период?"
        message={
          <div className="space-y-3">
            <p>
              Пробный период начнётся сразу и продлится 14 дней. Повторно активировать бесплатный
              период для этой аптеки нельзя.
            </p>
            {startTrial.isError && (
              <p
                className="rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-sm text-danger"
                role="alert"
              >
                {describeApiError(startTrial.error, "Не удалось начать пробный период")}
              </p>
            )}
          </div>
        }
        confirmLabel="Начать период"
        isLoading={startTrial.isPending}
        onConfirm={confirmTrial}
        onCancel={() => {
          if (startTrial.isPending) return;
          setConfirmOpen(false);
          setOperationId(null);
        }}
      />
    </div>
  );
}

function LaunchSummary({
  overview,
  title,
  description,
  tone,
  completed,
  total,
  progress,
  showProgress,
  firstAction,
  canOpenBilling,
}: {
  overview: OnboardingOverview;
  title: string;
  description: string;
  tone: "neutral" | "success" | "warning";
  completed: number;
  total: number;
  progress: number;
  showProgress: boolean;
  firstAction: { to: Parameters<typeof canAccessPath>[0]; label: string } | undefined;
  canOpenBilling: boolean;
}): JSX.Element {
  const setupPhase = overview.tenant_status === "setup";
  const primaryAction = firstAction ? (
    <Link to={firstAction.to} className={primaryLinkClass}>
      {firstAction.label}
      <ArrowRightIcon />
    </Link>
  ) : (overview.tenant_status === "grace_period" || overview.tenant_status === "readonly") &&
    canOpenBilling ? (
    <Link to="/billing" className={primaryLinkClass}>
      Открыть биллинг
      <ArrowRightIcon />
    </Link>
  ) : null;

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex flex-col gap-5 px-5 py-5 sm:px-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-sm text-foreground-muted">
            <Badge tone={tone === "success" ? "success" : tone === "warning" ? "warning" : "info"}>
              {setupPhase ? "Фаза настройки" : tenantStatusLabel[overview.tenant_status]}
            </Badge>
            <span aria-hidden="true">·</span>
            <span className="truncate">{overview.tenant_name}</span>
          </div>
          <h2 className="mt-3 text-xl font-semibold text-foreground">{title}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-5 text-foreground-secondary">
            {description}
          </p>
        </div>
        {primaryAction && <div className="shrink-0">{primaryAction}</div>}
      </div>

      {(showProgress || setupPhase) && (
        <div className="grid gap-3 border-t border-border px-5 py-4 sm:px-6 lg:grid-cols-[auto_auto_minmax(12rem,1fr)_auto] lg:items-center">
          <span className="text-sm font-medium text-foreground">Обязательные настройки</span>
          <span className="text-sm tabular-nums text-primary">
            {completed} из {total}
          </span>
          <div
            className="h-2 overflow-hidden rounded-full bg-foreground/10"
            role="progressbar"
            aria-label="Готовность обязательных настроек"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-normal"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="text-xs text-foreground-muted lg:text-right">
            {setupPhase
              ? `Фаза настройки доступна до ${formatDate(overview.setup_ends_at)}`
              : `${progress}% обязательных настроек выполнено`}
          </span>
        </div>
      )}
    </section>
  );
}

function ReadinessPanel({
  stepsByCode,
  activeStepCode,
  access,
}: {
  stepsByCode: Map<ReadinessStepCode, ReadinessStep>;
  activeStepCode: ReadinessStepCode | undefined;
  access: ReturnType<typeof getRouteAccessContext>;
}): JSX.Element {
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-5 py-4 sm:px-6">
        <h2 className="text-base font-semibold text-foreground">Готовность системы</h2>
      </div>
      <ol>
        {readinessSteps.map((definition, index) => {
          const step = stepsByCode.get(definition.code);
          if (!step) return null;
          const action = readinessStepAction(definition, step);
          const actionAvailable =
            !step.is_complete && action !== null && canAccessPath(action.to, access);
          const isActive = definition.code === activeStepCode;
          const isLocked = definition.code === "ready" && !step.is_complete;

          return (
            <li
              key={definition.code}
              className={cn(
                "relative grid min-h-16 min-w-0 grid-cols-[1.25rem_1.5rem_minmax(0,1fr)] items-center gap-x-3 border-b border-border px-4 py-3 last:border-b-0 sm:grid-cols-[1.25rem_1.5rem_minmax(10rem,0.8fr)_minmax(12rem,1.2fr)_auto] sm:px-5",
                isActive &&
                  "bg-warning-subtle/45 before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-warning",
              )}
            >
              <span className="text-sm tabular-nums text-foreground-secondary">{index + 1}</span>
              <StepStateIcon complete={step.is_complete} active={isActive} locked={isLocked} />
              <div className="min-w-0">
                <h3 className="font-medium text-foreground">{definition.title}</h3>
                <span className="sr-only">{step.is_complete ? "Выполнено" : "Не выполнено"}:</span>
              </div>
              <p className="col-start-3 mt-1 min-w-0 text-sm leading-5 text-foreground-muted sm:col-start-4 sm:mt-0">
                {definition.description}
              </p>
              <div className="col-start-3 mt-2 flex min-w-0 items-center justify-between gap-3 sm:col-start-5 sm:mt-0 sm:justify-end">
                <StepValue step={step} />
                {actionAvailable && action ? (
                  <Link
                    to={action.to}
                    aria-label={`Открыть раздел: ${definition.title}`}
                    className="inline-flex min-h-[var(--control-height-sm)] shrink-0 items-center gap-2 rounded-md border border-input bg-surface px-3 text-sm font-semibold text-foreground shadow-sm transition-colors duration-fast hover:border-primary hover:text-primary"
                  >
                    Открыть
                    <ArrowRightIcon />
                  </Link>
                ) : (
                  <span
                    className="grid h-9 w-9 shrink-0 place-items-center text-foreground-muted"
                    aria-hidden="true"
                  >
                    <ChevronRightIcon />
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function TrialPanel({
  overview,
  canStart,
  isOwner,
  online,
  onStart,
}: {
  overview: OnboardingOverview;
  canStart: boolean;
  isOwner: boolean;
  online: boolean;
  onStart: () => void;
}): JSX.Element {
  const isSetup = overview.tenant_status === "setup";
  const status = isSetup
    ? overview.is_ready
      ? "Готов к запуску"
      : "Ожидает готовности"
    : tenantStatusLabel[overview.tenant_status];
  const detail = isSetup
    ? "14 дней"
    : overview.tenant_status === "trial"
      ? `До ${formatDate(overview.trial_ends_at)}`
      : overview.tenant_status === "active"
        ? "Работа доступна"
        : "Требуется действие";

  return (
    <section className="rounded-lg border border-border bg-surface px-5 py-4">
      <h2 className="text-base font-semibold text-foreground">Пробный период</h2>
      <Badge className="mt-3" tone={overview.is_ready ? "success" : "warning"}>
        {status}
      </Badge>
      <div className="mt-4 text-2xl font-semibold tabular-nums text-foreground">{detail}</div>
      <p className="mt-1 text-sm leading-5 text-foreground-muted">
        {isSetup
          ? overview.is_ready
            ? "Все обязательные настройки завершены. Период начнётся после подтверждения."
            : "Начнётся после завершения обязательных настроек."
          : overview.tenant_status === "trial"
            ? "После окончания периода потребуется активная подписка."
            : "Состояние организации учитывается автоматически."}
      </p>

      {isSetup && (
        <Button className="mt-4 w-full" size="lg" onClick={onStart} disabled={!canStart || !online}>
          {!canStart && <LockIcon />}
          Начать пробный период
        </Button>
      )}
      {isSetup && overview.is_ready && !isOwner && (
        <p className="mt-2 text-xs text-foreground-muted" role="status">
          Запустить период может только владелец аптеки.
        </p>
      )}
      {isSetup && (
        <p className="mt-2 text-xs text-foreground-muted">
          Повторная бесплатная активация недоступна.
        </p>
      )}
    </section>
  );
}

function RecommendedPanel({
  overview,
  canOpenPos,
}: {
  overview: OnboardingOverview;
  canOpenPos: boolean;
}): JSX.Element {
  const posAvailable =
    overview.is_ready &&
    canOpenPos &&
    (overview.tenant_status === "trial" || overview.tenant_status === "active");

  return (
    <section className="rounded-lg border border-border bg-surface px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-foreground">Рекомендуется перед запуском</h2>
        <span className="text-sm tabular-nums text-foreground-muted">
          {overview.recommended_completed} из {overview.recommended_total}
        </span>
      </div>
      <ul className="mt-4 space-y-2.5">
        {overview.recommended_tasks.map((task) => (
          <li
            key={task.code}
            className={cn(
              "flex min-w-0 items-start gap-2 text-sm",
              task.is_complete ? "text-foreground-secondary" : "text-foreground",
            )}
          >
            <StepStateIcon complete={task.is_complete} active={false} locked={false} compact />
            <span>{taskLabel[task.code]}</span>
          </li>
        ))}
      </ul>

      {posAvailable ? (
        <Link to="/pos" className={cn(primaryLinkClass, "mt-5 w-full")}>
          Открыть кассу
          <ArrowRightIcon />
        </Link>
      ) : (
        <Button className="mt-5 w-full" size="lg" variant="secondary" disabled>
          <LockIcon />
          Открыть кассу
        </Button>
      )}
    </section>
  );
}

const tenantStatusLabel: Record<OnboardingOverview["tenant_status"], string> = {
  setup: "Фаза настройки",
  trial: "Пробный период",
  active: "Активна",
  grace_period: "Ожидает оплаты",
  readonly: "Только просмотр",
  archived: "Архив",
};

function StatusMark({ children }: { children: ReactNode }): JSX.Element {
  return (
    <span
      className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-danger text-xs font-bold text-danger"
      aria-hidden="true"
    >
      {children}
    </span>
  );
}

function StepStateIcon({
  complete,
  active,
  locked,
  compact = false,
}: {
  complete: boolean;
  active: boolean;
  locked: boolean;
  compact?: boolean;
}): JSX.Element {
  const size = compact ? "h-4 w-4" : "h-5 w-5";
  if (complete) {
    return (
      <span
        className={cn(
          "grid shrink-0 place-items-center rounded-full border border-success text-success",
          size,
        )}
        aria-hidden="true"
      >
        <CheckIcon compact={compact} />
      </span>
    );
  }
  if (locked) {
    return (
      <span
        className={cn(
          "grid shrink-0 place-items-center rounded-full border border-foreground/25 text-foreground-muted",
          size,
        )}
        aria-hidden="true"
      >
        <LockIcon compact />
      </span>
    );
  }
  return (
    <span
      className={cn(
        "shrink-0 rounded-full border-2",
        size,
        active ? "border-warning" : "border-foreground/25",
      )}
      aria-hidden="true"
    />
  );
}

function RefreshIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-5 w-5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="M20 6v5h-5" />
      <path d="M4 18v-5h5" />
      <path d="M6.1 9a7 7 0 0 1 11.6-2.5L20 11M4 13l2.3 4.5A7 7 0 0 0 17.9 15" />
    </svg>
  );
}

function ArrowRightIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="M4 10h12M11 5l5 5-5 5" />
    </svg>
  );
}

function ChevronRightIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="m7 4 6 6-6 6" />
    </svg>
  );
}

function CheckIcon({ compact = false }: { compact?: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className={compact ? "h-3 w-3" : "h-3.5 w-3.5"}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      aria-hidden="true"
    >
      <path d="m4 10 4 4 8-9" />
    </svg>
  );
}

function LockIcon({ compact = false }: { compact?: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className={compact ? "h-3 w-3" : "h-4 w-4"}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <rect x="4.5" y="8.5" width="11" height="8" rx="1.5" />
      <path d="M7 8.5V6a3 3 0 0 1 6 0v2.5" />
    </svg>
  );
}

function SyncIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      aria-hidden="true"
    >
      <path d="M16 6V3l-2 2a6 6 0 0 0-9.5 2M4 14v3l2-2a6 6 0 0 0 9.5-2" />
    </svg>
  );
}
