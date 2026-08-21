import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { canAccessPath, getRouteAccessContext } from "@/components/layout/routeAccess";
import { Badge, Button, ConfirmDialog, PageHeader, Skeleton } from "@/components/ui";
import { useMeQuery } from "@/features/auth/queries";
import { activeTenantId } from "@/features/auth/tenantContext";
import { describeApiError } from "@/lib/errorMessages";
import { cn } from "@/lib/utils";

import { readinessStepAction, readinessSteps, taskLabel } from "./labels";
import { useOnboardingOverviewQuery, useStartTrial } from "./queries";
import { type OnboardingOverview, type ReadinessStep } from "./types";

const primaryLinkClass =
  "inline-flex h-[var(--control-height-lg)] items-center justify-center gap-2 rounded-md border border-transparent bg-primary px-[var(--control-padding-lg)] text-base font-semibold text-primary-foreground shadow-sm transition-colors duration-fast hover:bg-primary/90 focus-visible:outline-none disabled:pointer-events-none";

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
    <span className="whitespace-nowrap text-xs font-medium text-foreground-muted">
      {step.current} из {step.target}
    </span>
  );
}

function OnboardingSkeleton(): JSX.Element {
  return (
    <div className="mx-auto max-w-[960px] space-y-5" aria-label="Загрузка готовности">
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-72 w-full" />
      <Skeleton className="h-36 w-full" />
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
      <div className="mx-auto max-w-[720px] rounded-md border border-danger/25 bg-danger/5 p-6">
        <div className="flex items-start gap-3">
          <span
            className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-danger text-xs font-bold text-danger"
            aria-hidden="true"
          >
            !
          </span>
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
  const firstAction = readinessSteps
    .map((definition) => {
      const step = stepsByCode.get(definition.code);
      const action = step ? readinessStepAction(definition, step) : null;
      return step?.required && !step.is_complete && action ? action : null;
    })
    .find((action) => action !== null && canAccessPath(action.to, access));
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
    <div className="mx-auto max-w-[960px] space-y-5 pb-8">
      <PageHeader
        title="Старт"
        description="Точная проверка готовности аптеки к ежедневной работе"
      />

      {!online && (
        <div
          className="flex items-center gap-2 rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-foreground-secondary"
          role="status"
        >
          <span className="font-semibold text-warning">Нет сети.</span>
          Показаны последние сохранённые данные. Изменения станут доступны после подключения.
        </div>
      )}

      <section className="rounded-md border border-border bg-surface px-5 py-5 shadow-sm sm:px-6">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-foreground">{copy.title}</h2>
              <Badge
                tone={
                  copy.tone === "success"
                    ? "success"
                    : copy.tone === "warning"
                      ? "warning"
                      : "neutral"
                }
              >
                {overview.tenant_name}
              </Badge>
            </div>
            <p className="mt-2 max-w-2xl text-sm leading-5 text-foreground-secondary">
              {copy.description}
            </p>
          </div>

          {showReadinessProgress && firstAction && (
            <Link to={firstAction.to} className={primaryLinkClass}>
              {firstAction.label}
              <span aria-hidden="true">→</span>
            </Link>
          )}
          {trialCanBeStarted && (
            <Button size="lg" onClick={openTrialConfirmation} disabled={!online}>
              Начать пробный период
            </Button>
          )}
          {(overview.tenant_status === "trial" || overview.tenant_status === "active") &&
            (overview.is_ready || !firstAction) &&
            canOpenPos && (
              <Link to="/pos" className={primaryLinkClass}>
                Открыть кассу
                <span aria-hidden="true">→</span>
              </Link>
            )}
          {(overview.tenant_status === "grace_period" || overview.tenant_status === "readonly") &&
            canOpenBilling && (
              <Link to="/billing" className={primaryLinkClass}>
                Открыть биллинг
                <span aria-hidden="true">→</span>
              </Link>
            )}
        </div>

        {showReadinessProgress && (
          <div className="mt-6">
            <div className="mb-2 flex items-center justify-between gap-4 text-sm">
              <span className="font-medium text-foreground">Обязательные настройки</span>
              <span className="tabular-nums text-foreground-muted">
                {completed} из {total}
              </span>
            </div>
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
            <div className="mt-2 text-xs text-foreground-muted">Проверено в {checkedAt}</div>
            {overview.tenant_status === "setup" && (
              <div className="mt-1 text-xs text-foreground-muted">
                Фаза настройки доступна до {formatDate(overview.setup_ends_at)}
              </div>
            )}
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-md border border-border bg-surface shadow-sm">
        <div className="border-b border-border px-5 py-4 sm:px-6">
          <h2 className="text-base font-semibold text-foreground">Готовность системы</h2>
        </div>
        <ol className="divide-y divide-border">
          {readinessSteps.map((definition) => {
            const step = stepsByCode.get(definition.code);
            if (!step) return null;
            const action = readinessStepAction(definition, step);
            const actionAvailable = !step.is_complete && action && canAccessPath(action.to, access);
            return (
              <li
                key={definition.code}
                className="flex min-w-0 items-start gap-3 px-5 py-4 sm:px-6"
              >
                <span className="sr-only">
                  {step.is_complete ? "Выполнено" : "Не выполнено"}:
                </span>
                {step.is_complete ? (
                  <span
                    className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-success text-xs font-bold text-success-contrast"
                    aria-hidden="true"
                  >
                    ✓
                  </span>
                ) : (
                  <span
                    className="mt-0.5 h-5 w-5 shrink-0 rounded-full border-2 border-foreground/20"
                    aria-hidden="true"
                  />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                    <h3 className="font-medium text-foreground">{definition.title}</h3>
                    <StepValue step={step} />
                  </div>
                  <p className="mt-1 text-sm leading-5 text-foreground-muted">
                    {definition.description}
                  </p>
                </div>
                {actionAvailable && action && (
                  <Link
                    to={action.to}
                    className="shrink-0 rounded-sm px-2 py-1 text-sm font-semibold text-primary hover:bg-primary/5 focus-visible:outline-none"
                  >
                    Открыть
                  </Link>
                )}
              </li>
            );
          })}
        </ol>
      </section>

      <section className="rounded-md border border-border bg-surface px-5 py-5 shadow-sm sm:px-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-base font-semibold text-foreground">Рекомендуется перед запуском</h2>
          <span className="text-xs tabular-nums text-foreground-muted">
            {overview.recommended_completed} из {overview.recommended_total}
          </span>
        </div>
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {overview.recommended_tasks.map((task) => (
            <li
              key={task.code}
              className={cn(
                "flex min-w-0 items-center gap-2 text-sm",
                task.is_complete ? "text-foreground-secondary" : "text-foreground",
              )}
            >
              <span className="sr-only">
                {task.is_complete ? "Выполнено" : "Не выполнено"}:
              </span>
              {task.is_complete ? (
                <span className="shrink-0 font-bold text-success" aria-hidden="true">
                  ✓
                </span>
              ) : (
                <span
                  className="h-4 w-4 shrink-0 rounded-full border border-foreground/20"
                  aria-hidden="true"
                />
              )}
              <span>{taskLabel[task.code]}</span>
            </li>
          ))}
        </ul>
      </section>

      {overview.tenant_status === "setup" && overview.is_ready && !access.isTenantOwner && (
        <p className="text-center text-sm text-foreground-muted" role="status">
          Запустить пробный период может только владелец аптеки.
        </p>
      )}

      {startTrial.isError && !confirmOpen && (
        <div
          className="rounded-md border border-danger/25 bg-danger/5 px-4 py-3 text-sm text-danger"
          role="alert"
        >
          {describeApiError(startTrial.error, "Не удалось начать пробный период")}
        </div>
      )}

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
