import { useState } from "react";
import { Link } from "@tanstack/react-router";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { cn } from "@/lib/utils";

import { taskLabel, wizardSteps } from "./labels";
import {
  useChecklistQuery,
  useStartTrial,
  useSubmitStep,
  useWizardQuery,
} from "./queries";

const TRIAL_MIN_CATALOG = 100;

export function OnboardingPage(): JSX.Element {
  const wizard = useWizardQuery();
  const checklist = useChecklistQuery();
  const submitStep = useSubmitStep();
  const startTrial = useStartTrial();
  const [topError, setTopError] = useState<string | null>(null);
  const [trialBanner, setTrialBanner] = useState<string | null>(null);

  const onComplete = async (step: number) => {
    setTopError(null);
    try {
      await submitStep.mutateAsync({
        step,
        payload: { noted_at: new Date().toISOString() },
      });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось зафиксировать шаг"));
    }
  };

  const onStartTrial = async () => {
    setTopError(null);
    try {
      const r = await startTrial.mutateAsync();
      setTrialBanner(
        `🎉 Пробный период активирован до ${new Date(r.trial_ends_at).toLocaleDateString("ru-RU")}`,
      );
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось активировать пробный период"));
    }
  };

  if (wizard.isLoading || checklist.isLoading) {
    return <p className="text-sm text-slate-500">Загрузка…</p>;
  }
  if (wizard.error || !wizard.data) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-slate-900">Старт</h1>
        <p className="text-sm text-red-600">
          {describeApiError(wizard.error, "Не удалось загрузить визард")}
        </p>
      </div>
    );
  }

  const w = wizard.data;
  const c = checklist.data;
  const completedSet = new Set(w.steps_completed);
  const progressPct = Math.round((w.steps_completed.length / wizardSteps.length) * 100);
  const catalogPct = c
    ? Math.min(100, Math.round((c.catalog_items_count / TRIAL_MIN_CATALOG) * 100))
    : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Старт</h1>
        {w.is_completed ? (
          <Badge tone="success">Визард завершён</Badge>
        ) : (
          <span className="text-sm text-slate-500">
            {w.steps_completed.length} из {wizardSteps.length}
          </span>
        )}
      </div>

      {trialBanner && (
        <p className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          {trialBanner}
        </p>
      )}
      {topError && <p className="text-sm text-red-600">{topError}</p>}

      <Card>
        <CardHeader>
          <CardTitle>Мастер настройки</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
            <div
              className="h-full bg-emerald-500 transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <ol className="space-y-2 pt-2">
            {wizardSteps.map((s) => {
              const done = completedSet.has(s.step);
              const isCurrent = w.current_step === s.step && !done;
              return (
                <li
                  key={s.step}
                  className={cn(
                    "rounded-md border px-3 py-2",
                    done
                      ? "border-emerald-200 bg-emerald-50"
                      : isCurrent
                        ? "border-slate-300 bg-white"
                        : "border-slate-200 bg-white",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={cn(
                            "inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold",
                            done
                              ? "bg-emerald-600 text-white"
                              : "bg-slate-200 text-slate-700",
                          )}
                        >
                          {done ? "✓" : s.step}
                        </span>
                        <span className="font-medium text-slate-900">{s.title}</span>
                        {isCurrent && <Badge tone="info">текущий</Badge>}
                      </div>
                      <p className="ml-8 mt-1 text-sm text-slate-600">{s.description}</p>
                      {s.linkTo && (
                        <Link
                          to={s.linkTo}
                          className="ml-8 mt-1 inline-block text-sm text-slate-700 underline hover:text-slate-900"
                        >
                          {s.linkLabel ?? "Открыть"}
                        </Link>
                      )}
                    </div>
                    {!done && !w.is_completed && (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => void onComplete(s.step)}
                        isLoading={submitStep.isPending && submitStep.variables?.step === s.step}
                      >
                        Отметить
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </CardContent>
      </Card>

      {c && (
        <Card>
          <CardHeader>
            <CardTitle>Чек-лист первых задач</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <div className="flex justify-between text-sm">
                <span className="text-slate-600">Каталог</span>
                <span className="font-mono">
                  {c.catalog_items_count} / {TRIAL_MIN_CATALOG}
                </span>
              </div>
              <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-200">
                <div
                  className={cn(
                    "h-full transition-all",
                    c.catalog_items_count >= TRIAL_MIN_CATALOG
                      ? "bg-emerald-500"
                      : "bg-amber-500",
                  )}
                  style={{ width: `${catalogPct}%` }}
                />
              </div>
            </div>

            <div>
              <p className="text-sm font-medium text-slate-700">Задачи</p>
              {c.completed_tasks.length === 0 ? (
                <p className="mt-1 text-sm italic text-slate-500">
                  Действия в системе автоматически отмечаются здесь.
                </p>
              ) : (
                <div className="mt-2 flex flex-wrap gap-1">
                  {c.completed_tasks.map((t) => (
                    <Badge key={t} tone="success">
                      ✓ {taskLabel[t] ?? t}
                    </Badge>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <p className="text-xs text-slate-500">Право на trial</p>
                {c.trial_eligible ? (
                  <Badge tone="success">Доступен</Badge>
                ) : (
                  <Badge tone="neutral">Каталог &lt; 100</Badge>
                )}
              </div>
              <div>
                <p className="text-xs text-slate-500">Фаза настройки до</p>
                <p>{new Date(c.setup_ends_at).toLocaleDateString("ru-RU")}</p>
              </div>
              {c.trial_started_at && (
                <div className="col-span-2">
                  <p className="text-xs text-slate-500">Trial начат</p>
                  <p>{new Date(c.trial_started_at).toLocaleString("ru-RU")}</p>
                </div>
              )}
            </div>

            {c.trial_eligible && !c.trial_started_at && (
              <Button onClick={() => void onStartTrial()} isLoading={startTrial.isPending}>
                Запустить пробный период (14 дней)
              </Button>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
