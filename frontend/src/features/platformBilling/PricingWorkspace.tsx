import { useEffect, useState } from "react";

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
import { formatBillingMoney } from "@/features/billing/format";
import { describeApiError } from "@/lib/errorMessages";

import { PricingCommandModal, type PricingCommandTarget } from "./PricingCommandModal";
import { formatPricingDateTime } from "./pricingTime";
import { usePlatformPricingPlans } from "./queries";
import {
  type PlatformPricingPlan,
  type PlatformPricingStatus,
  type PlatformPricingVersion,
} from "./types";
import { useOnlineStatus } from "./useOnlineStatus";

const PAGE_SIZE = 20;

const statusLabel: Record<PlatformPricingStatus, string> = {
  draft: "Черновик",
  scheduled: "Запланирована",
  active: "Действует",
  archived: "В архиве",
  cancelled: "Отменена",
};

const statusTone: Record<
  PlatformPricingStatus,
  "neutral" | "info" | "success" | "warning" | "danger"
> = {
  draft: "neutral",
  scheduled: "info",
  active: "success",
  archived: "neutral",
  cancelled: "danger",
};

interface Props {
  canManage: boolean;
  currentUserId: string;
  onFetchingChange?: (fetching: boolean) => void;
  refreshSignal: number;
}

export function PricingWorkspace({
  canManage,
  currentUserId,
  onFetchingChange,
  refreshSignal,
}: Props): JSX.Element {
  const [page, setPage] = useState(1);
  const [command, setCommand] = useState<PricingCommandTarget | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const online = useOnlineStatus();
  const plans = usePlatformPricingPlans(page, PAGE_SIZE, true);
  const refetchPlans = plans.refetch;

  useEffect(() => {
    onFetchingChange?.(plans.isFetching);
  }, [onFetchingChange, plans.isFetching]);

  useEffect(() => {
    if (refreshSignal === 0) return;
    void refetchPlans();
  }, [refetchPlans, refreshSignal]);

  useEffect(() => {
    const total = plans.data?.total;
    if (total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [page, plans.data?.total]);

  const refreshAfterConflict = (message: string) => {
    setNotice(message);
    void plans.refetch();
  };

  return (
    <section className="space-y-3" aria-labelledby="pricing-plans-heading">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="pricing-plans-heading" className="text-base font-semibold text-foreground">
            Тарифы и версии цен
          </h2>
          <p className="mt-0.5 text-xs text-foreground-muted">
            Публикация цены требует подтверждения другим уполномоченным сотрудником.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {plans.data ? <Badge tone="neutral">{plans.data.total} тарифов</Badge> : null}
          {canManage ? (
            <Button
              size="sm"
              disabled={!online}
              onClick={() => setCommand({ kind: "create-plan" })}
            >
              Создать тариф
            </Button>
          ) : null}
        </div>
      </div>

      {!online ? (
        <div
          className="rounded-lg border border-warning/30 bg-warning-subtle px-4 py-3 text-sm text-warning-foreground"
          role="status"
        >
          Нет подключения. Тарифы доступны для просмотра, финансовые команды временно отключены.
        </div>
      ) : null}

      {notice ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-info/30 bg-info-subtle px-4 py-3 text-sm text-info-foreground"
          role="status"
        >
          <span>{notice}</span>
          <Button size="sm" variant="ghost" onClick={() => setNotice(null)}>
            Закрыть
          </Button>
        </div>
      ) : null}

      {plans.isLoading ? (
        <SkeletonRows rows={6} />
      ) : plans.error && !plans.data ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3"
          role="alert"
        >
          <p className="text-sm text-danger-foreground">
            {describeApiError(plans.error, "Не удалось загрузить тарифы")}
          </p>
          <Button
            variant="secondary"
            size="sm"
            isLoading={plans.isFetching}
            onClick={() => void plans.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : plans.data?.items.length === 0 ? (
        <TableEmpty title="Тарифы ещё не созданы">
          {canManage
            ? "Создайте первый тариф, затем добавьте и согласуйте цену."
            : "У вас есть доступ к просмотру, но тарифы пока отсутствуют."}
        </TableEmpty>
      ) : plans.data ? (
        <div className="space-y-3">
          {plans.data.items.map((plan) => (
            <PricingPlanPanel
              key={plan.plan_id}
              plan={plan}
              canManage={canManage}
              online={online}
              currentUserId={currentUserId}
              onCommand={setCommand}
            />
          ))}
          {plans.data.total > PAGE_SIZE ? (
            <Pagination
              page={plans.data.page}
              pageSize={PAGE_SIZE}
              total={plans.data.total}
              onPage={setPage}
            />
          ) : null}
        </div>
      ) : null}

      <PricingCommandModal
        target={command}
        online={online}
        onClose={() => setCommand(null)}
        onCompleted={(message) => {
          setCommand(null);
          setNotice(message);
        }}
        onRefreshRequired={refreshAfterConflict}
      />
    </section>
  );
}

function PricingPlanPanel({
  plan,
  canManage,
  online,
  currentUserId,
  onCommand,
}: {
  plan: PlatformPricingPlan;
  canManage: boolean;
  online: boolean;
  currentUserId: string;
  onCommand: (target: PricingCommandTarget) => void;
}): JSX.Element {
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="break-words font-semibold text-foreground">{plan.name}</h3>
            <Badge tone={plan.is_active ? "success" : "neutral"}>
              {plan.is_active ? "Опубликован" : "Без активной цены"}
            </Badge>
          </div>
          <p className="mt-1 font-mono text-xs text-foreground-muted">{plan.code}</p>
          {plan.description ? (
            <p className="mt-2 max-w-3xl break-words text-sm text-foreground-secondary">
              {plan.description}
            </p>
          ) : null}
        </div>
        {canManage ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={!online}
            onClick={() => onCommand({ kind: "create-price", plan })}
          >
            Новая цена
          </Button>
        ) : null}
      </div>

      {plan.versions.length === 0 ? (
        <div className="px-4 py-5 text-sm text-foreground-muted">Версий цен пока нет.</div>
      ) : (
        <PricingVersions
          plan={plan}
          canManage={canManage}
          online={online}
          currentUserId={currentUserId}
          onCommand={onCommand}
        />
      )}
    </Card>
  );
}

function PricingVersions({
  plan,
  canManage,
  online,
  currentUserId,
  onCommand,
}: {
  plan: PlatformPricingPlan;
  canManage: boolean;
  online: boolean;
  currentUserId: string;
  onCommand: (target: PricingCommandTarget) => void;
}): JSX.Element {
  return (
    <>
      <div className="hidden lg:block">
        <Table aria-label={`Версии цен тарифа ${plan.name}`}>
          <THead>
            <TR>
              <TH>Версия</TH>
              <TH>Аудитория</TH>
              <TH className="text-right">В месяц</TH>
              <TH className="text-right">Годовая скидка</TH>
              <TH>Вступает в силу</TH>
              <TH>Статус</TH>
              {canManage ? <TH className="text-right">Действия</TH> : null}
            </TR>
          </THead>
          <TBody>
            {plan.versions.map((version) => (
              <TR key={version.price_version_id}>
                <TD className="font-medium">№ {version.version_number}</TD>
                <TD>{audienceLabel(version)}</TD>
                <TD className="whitespace-nowrap text-right font-semibold tabular-nums">
                  {formatBillingMoney(version.monthly_price_per_branch, version.currency)}
                </TD>
                <TD className="whitespace-nowrap text-right tabular-nums">
                  {formatPercent(version.annual_discount_pct)}
                </TD>
                <TD className="whitespace-nowrap">
                  {version.effective_from
                    ? formatPricingDateTime(version.effective_from)
                    : "Не назначена"}
                </TD>
                <TD>
                  <Badge tone={statusTone[version.status]}>{statusLabel[version.status]}</Badge>
                </TD>
                {canManage ? (
                  <TD>
                    <VersionActions
                      plan={plan}
                      version={version}
                      currentUserId={currentUserId}
                      online={online}
                      onCommand={onCommand}
                    />
                  </TD>
                ) : null}
              </TR>
            ))}
          </TBody>
        </Table>
      </div>
      <ul className="divide-y divide-border lg:hidden">
        {plan.versions.map((version) => (
          <li key={version.price_version_id} className="px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-semibold text-foreground">Версия № {version.version_number}</p>
                <p className="mt-1 text-xs text-foreground-muted">{audienceLabel(version)}</p>
              </div>
              <Badge tone={statusTone[version.status]}>{statusLabel[version.status]}</Badge>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-border pt-3 text-sm">
              <div>
                <dt className="text-xs text-foreground-muted">Цена в месяц</dt>
                <dd className="mt-1 font-semibold tabular-nums">
                  {formatBillingMoney(version.monthly_price_per_branch, version.currency)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-foreground-muted">Годовая скидка</dt>
                <dd className="mt-1 tabular-nums">{formatPercent(version.annual_discount_pct)}</dd>
              </div>
            </dl>
            {canManage ? (
              <div className="mt-3">
                <VersionActions
                  plan={plan}
                  version={version}
                  currentUserId={currentUserId}
                  online={online}
                  onCommand={onCommand}
                />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  );
}

function VersionActions({
  plan,
  version,
  currentUserId,
  online,
  onCommand,
}: {
  plan: PlatformPricingPlan;
  version: PlatformPricingVersion;
  currentUserId: string;
  online: boolean;
  onCommand: (target: PricingCommandTarget) => void;
}): JSX.Element | null {
  const isAuthor = version.created_by === currentUserId;
  if (version.status === "draft") {
    if (isAuthor) {
      return (
        <span className="block text-right text-xs text-foreground-muted">
          Ожидает другого согласующего
        </span>
      );
    }
    return (
      <div className="flex justify-end">
        <Button
          size="sm"
          variant="secondary"
          disabled={!online}
          onClick={() => onCommand({ kind: "schedule", plan, version })}
        >
          Согласовать
        </Button>
      </div>
    );
  }
  if (version.status !== "scheduled") return null;

  const canActivate =
    version.effective_from !== null && new Date(version.effective_from).getTime() <= Date.now();
  return (
    <div className="flex flex-wrap justify-end gap-2">
      {canActivate ? (
        <Button
          size="sm"
          variant="success"
          disabled={!online}
          onClick={() => onCommand({ kind: "activate", plan, version })}
        >
          Активировать
        </Button>
      ) : null}
      <Button
        size="sm"
        variant="ghost"
        disabled={!online}
        onClick={() => onCommand({ kind: "cancel", plan, version })}
      >
        Отменить
      </Button>
    </div>
  );
}

function audienceLabel(version: PlatformPricingVersion): string {
  return version.audience === "default" ? "Все клиенты" : "Новые клиенты";
}

function formatPercent(value: string): string {
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(Number(value))} %`;
}
