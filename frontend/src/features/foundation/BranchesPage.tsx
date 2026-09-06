import { useEffect, useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  ConfirmDialog,
  Input,
  Label,
  Modal,
  Pagination,
  Select,
  SkeletonRows,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";

import { BranchForm } from "./BranchForm";
import { describeApiError, describeFoundationError } from "./errors";
import { LocationsSummary, LocationsWorkspaceHeader } from "./LocationsWorkspace";
import {
  useBranchLifecycleImpactQuery,
  useBranchSearchQuery,
  useDeleteBranch,
  useUpdateBranch,
} from "./queries";
import { type Branch, type BranchLifecycleImpact, type BranchType } from "./types";

const PAGE_SIZE = 25;

const branchTypeLabel: Record<BranchType, string> = {
  pharmacy: "Аптека",
  pharmacy_post: "Аптечный пункт",
  kiosk: "Киоск",
};

type StatusFilter = "active" | "inactive" | "all";

export function BranchesPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("branches");
  const canCreate = hasPermission(user, "branches.create");
  const canUpdate = hasPermission(user, "branches.update");
  const canDelete = hasPermission(user, "branches.delete");
  const canViewRegisters = hasPermission(user, "registers.view");
  const showActions = canUpdate || canDelete;
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [branchType, setBranchType] = useState<BranchType | "">("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Branch | null>(null);
  const [creating, setCreating] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [lifecycleTarget, setLifecycleTarget] = useState<Branch | null>(null);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const { data, isPending, isFetching, fetchStatus, error, refetch } = useBranchSearchQuery({
    q,
    branch_type: branchType || undefined,
    is_active: status === "all" ? undefined : status === "active",
    page,
    page_size: PAGE_SIZE,
  });
  const deleteMutation = useDeleteBranch();
  const updateMutation = useUpdateBranch();
  const lifecycleImpact = useBranchLifecycleImpactQuery(
    lifecycleTarget?.id ?? null,
    lifecycleTarget !== null,
  );
  const rows = data?.items ?? [];
  const hasInitialLoadError = Boolean(error && data === undefined);
  const hasFilters = Boolean(q || branchType || status !== "active");
  const activeOnPage = rows.filter((branch) => branch.is_active).length;
  const licenseAttentionOnPage = rows.filter((branch) =>
    licenseNeedsAttention(branch.license_number, branch.license_expires_at),
  ).length;

  const closeEditor = () => {
    setCreating(false);
    setEditing(null);
    setEditorDirty(false);
    setDiscardOpen(false);
  };

  const requestEditorClose = () => {
    if (editorDirty) setDiscardOpen(true);
    else closeEditor();
  };

  const confirmLifecycleChange = async () => {
    if (!lifecycleTarget) return;
    setLifecycleError(null);
    try {
      if (lifecycleTarget.is_active) {
        if (!canDelete || !lifecycleImpact.data?.can_deactivate) return;
        await deleteMutation.mutateAsync(lifecycleTarget.id);
      } else {
        if (!canUpdate) return;
        await updateMutation.mutateAsync({
          id: lifecycleTarget.id,
          payload: { is_active: true },
        });
      }
      setLifecycleTarget(null);
    } catch (err) {
      setLifecycleError(
        describeFoundationError(
          err,
          lifecycleTarget.is_active
            ? "Не удалось отключить торговую точку"
            : "Не удалось восстановить торговую точку",
        ),
      );
    }
  };

  return (
    <div className="space-y-4">
      <LocationsWorkspaceHeader
        active="branches"
        showBranches
        showRegisters={canViewRegisters}
        meta={isFetching && !isPending ? "Обновление…" : undefined}
        actions={
          canCreate ? (
            <Button onClick={() => setCreating(true)}>Добавить торговую точку</Button>
          ) : undefined
        }
      />

      {!hasInitialLoadError && (
        <LocationsSummary
          label="Сводка торговых точек"
          loading={isPending}
          metrics={[
            { label: "Всего по фильтрам", value: data?.total ?? 0 },
            { label: "Показано на странице", value: rows.length },
            { label: "Активных на странице", value: activeOnPage, tone: "success" },
            {
              label: "Лицензии требуют внимания на странице",
              value: licenseAttentionOnPage,
              tone: licenseAttentionOnPage > 0 ? "warning" : "default",
            },
          ]}
        />
      )}

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div className="w-64 sm:w-72">
                <Label htmlFor="branch_search">Поиск</Label>
                <Input
                  id="branch_search"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Название, адрес или лицензия"
                />
              </div>
            ),
            active: Boolean(qInput.trim()),
            onClear: () => {
              setQInput("");
              setQ("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "type",
            label: "Тип торговой точки",
            content: (
              <div>
                <Label htmlFor="branch_type_filter">Тип торговой точки</Label>
                <Select
                  id="branch_type_filter"
                  value={branchType}
                  onChange={(event) => {
                    setBranchType(event.target.value as BranchType | "");
                    setPage(1);
                  }}
                  className="w-44"
                >
                  <option value="">Все типы</option>
                  {(Object.keys(branchTypeLabel) as BranchType[]).map((type) => (
                    <option key={type} value={type}>
                      {branchTypeLabel[type]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(branchType),
            onClear: () => {
              setBranchType("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "status",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="branch_status_filter">Статус</Label>
                <Select
                  id="branch_status_filter"
                  value={status}
                  onChange={(event) => {
                    setStatus(event.target.value as StatusFilter);
                    setPage(1);
                  }}
                  className="w-40"
                >
                  <option value="active">Активные</option>
                  <option value="inactive">Неактивные</option>
                  <option value="all">Все</option>
                </Select>
              </div>
            ),
            active: status !== "active",
            onClear: () => {
              setStatus("active");
              setPage(1);
            },
            defaultVisible: true,
          },
        ]}
        onResetValues={() => {
          setQInput("");
          setQ("");
          setBranchType("");
          setStatus("active");
          setPage(1);
        }}
      />

      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm leading-5 text-danger-foreground"
        >
          <span>{describeApiError(error, "Не удалось загрузить торговые точки")}</span>
          <Button variant="secondary" size="sm" onClick={() => void refetch()}>
            Повторить
          </Button>
        </div>
      )}
      {isPending ? (
        <>
          {fetchStatus === "paused" && (
            <p role="status" className="text-sm text-foreground-muted">
              Нет связи с сервером. Список загрузится автоматически после восстановления соединения.
            </p>
          )}
          <SkeletonRows rows={6} />
        </>
      ) : hasInitialLoadError ? null : rows.length === 0 ? (
        hasFilters ? (
          <TableEmpty title="Ничего не найдено">Измените запрос или выбранные фильтры.</TableEmpty>
        ) : (
          <TableEmpty
            title="Торговых точек пока нет"
            action={
              canCreate ? (
                <Button onClick={() => setCreating(true)}>Добавить торговую точку</Button>
              ) : undefined
            }
          >
            Добавьте первую аптеку или аптечный пункт, чтобы подключить кассы и вести остатки.
          </TableEmpty>
        )
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Название</TH>
                <TH>Тип</TH>
                <TH>Адрес</TH>
                <TH>Лицензия</TH>
                <TH>Реквизиты чека</TH>
                <TH>Статус</TH>
                {showActions && <TH className="text-right">Действия</TH>}
              </TR>
            </THead>
            <TBody>
              {rows.map((branch) => (
                <TR key={branch.id}>
                  <TD>
                    <span className="block font-semibold">{branch.name}</span>
                    <span className="mt-0.5 block text-xs text-foreground-muted">
                      Создана {new Date(branch.created_at).toLocaleDateString("ru-RU")}
                    </span>
                  </TD>
                  <TD>
                    <Badge tone="neutral">{branchTypeLabel[branch.branch_type]}</Badge>
                  </TD>
                  <TD className="max-w-sm whitespace-normal leading-5">
                    {branch.address ?? <span className="text-foreground-muted">Не указан</span>}
                  </TD>
                  <TD>{renderLicense(branch.license_number, branch.license_expires_at)}</TD>
                  <TD>
                    {branch.receipt_header?.line1 ? (
                      <Badge tone="success">Заполнены</Badge>
                    ) : (
                      <Badge tone="warning">Нужно заполнить</Badge>
                    )}
                  </TD>
                  <TD>
                    {branch.is_active ? (
                      <Badge tone="success">Активна</Badge>
                    ) : (
                      <Badge tone="neutral">Неактивна</Badge>
                    )}
                  </TD>
                  {showActions && (
                    <TD className="text-right">
                      {canUpdate && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={isFetching}
                          onClick={() => setEditing(branch)}
                        >
                          Открыть
                        </Button>
                      )}
                      {((canDelete && branch.is_active) || (canUpdate && !branch.is_active)) && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={isFetching}
                          onClick={() => {
                            setLifecycleError(null);
                            setLifecycleTarget(branch);
                          }}
                        >
                          {branch.is_active ? "Отключить" : "Восстановить"}
                        </Button>
                      )}
                    </TD>
                  )}
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} onPage={setPage} />
        </>
      )}

      {(canCreate || canUpdate) && (
        <Modal
          open={creating || editing !== null}
          onClose={requestEditorClose}
          title={editing ? `Изменить торговую точку: ${editing.name}` : "Добавить торговую точку"}
        >
          <BranchForm
            branch={editing}
            onClose={closeEditor}
            onCancel={requestEditorClose}
            onDirtyChange={setEditorDirty}
          />
        </Modal>
      )}
      <Modal
        open={lifecycleTarget !== null}
        onClose={() => {
          if (deleteMutation.isPending || updateMutation.isPending) return;
          setLifecycleTarget(null);
          setLifecycleError(null);
        }}
        title={
          lifecycleTarget?.is_active
            ? `Отключить точку: ${lifecycleTarget?.name ?? ""}`
            : `Восстановить точку: ${lifecycleTarget?.name ?? ""}`
        }
      >
        <div className="space-y-4 text-sm leading-5">
          {lifecycleImpact.isLoading ? (
            <div role="status" className="text-foreground-muted">
              Проверяем смены, кассы и доступ сотрудников…
            </div>
          ) : lifecycleImpact.error ? (
            <div role="alert" className="rounded-md border border-danger/30 bg-danger-subtle p-3">
              <p className="text-danger-foreground">
                {describeApiError(lifecycleImpact.error, "Не удалось проверить состояние точки")}
              </p>
              <Button
                className="mt-3"
                variant="secondary"
                size="sm"
                onClick={() => void lifecycleImpact.refetch()}
              >
                Повторить проверку
              </Button>
            </div>
          ) : lifecycleImpact.data ? (
            <>
              {lifecycleTarget?.is_active ? (
                <BranchDeactivationSummary impact={lifecycleImpact.data} />
              ) : (
                <BranchRestorationSummary impact={lifecycleImpact.data} />
              )}
              {lifecycleError && (
                <div
                  role="alert"
                  className="rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-danger-foreground"
                >
                  {lifecycleError}
                </div>
              )}
              <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
                <Button variant="secondary" onClick={() => setLifecycleTarget(null)}>
                  Отмена
                </Button>
                <Button
                  variant={lifecycleTarget?.is_active ? "danger" : "primary"}
                  disabled={Boolean(
                    lifecycleTarget?.is_active && !lifecycleImpact.data.can_deactivate,
                  )}
                  isLoading={deleteMutation.isPending || updateMutation.isPending}
                  onClick={() => void confirmLifecycleChange()}
                >
                  {lifecycleTarget?.is_active ? "Отключить точку" : "Восстановить точку"}
                </Button>
              </div>
            </>
          ) : null}
        </div>
      </Modal>
      <ConfirmDialog
        open={discardOpen}
        title="Закрыть без сохранения?"
        message="Изменения торговой точки не сохранятся."
        cancelLabel="Продолжить редактирование"
        confirmLabel="Закрыть без сохранения"
        variant="danger"
        onCancel={() => setDiscardOpen(false)}
        onConfirm={closeEditor}
      />
    </div>
  );
}

function BranchDeactivationSummary({ impact }: { impact: BranchLifecycleImpact }): JSX.Element {
  const blockedByShift = impact.open_shift_count > 0;
  const blockedAsLastBranch = !impact.can_deactivate && !blockedByShift;

  return (
    <>
      <div
        className={
          impact.can_deactivate
            ? "rounded-md border border-warning/35 bg-warning-subtle p-3"
            : "rounded-md border border-danger/30 bg-danger-subtle p-3"
        }
      >
        <p className="font-semibold">
          {impact.can_deactivate
            ? "Точка готова к безопасному отключению"
            : "Сейчас отключить точку нельзя"}
        </p>
        {blockedByShift && (
          <p className="mt-1">
            Сначала закройте {impact.open_shift_count} {pluralizeShift(impact.open_shift_count)}.
          </p>
        )}
        {blockedAsLastBranch && (
          <p className="mt-1">У организации должна оставаться хотя бы одна активная точка.</p>
        )}
      </div>
      <LifecycleCounts impact={impact} />
      <div>
        <p className="font-semibold">После отключения</p>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-foreground-secondary">
          <li>Все активные кассы этой точки будут выключены.</li>
          <li>Доступ назначенных сотрудников временно перестанет действовать.</li>
          <li>Синхронизация Edge и офлайн-касс этой точки будет приостановлена.</li>
          <li>Продажи, чеки, остатки, назначения и история сохранятся.</li>
        </ul>
      </div>
    </>
  );
}

function BranchRestorationSummary({ impact }: { impact: BranchLifecycleImpact }): JSX.Element {
  return (
    <>
      <div className="rounded-md border border-primary/25 bg-primary-subtle p-3">
        <p className="font-semibold">Данные точки сохранены и готовы к восстановлению</p>
        <p className="mt-1 text-foreground-secondary">
          Восстановление включает саму точку. Рабочие кассы останутся выключенными, пока владелец не
          проверит и не включит каждую из них.
        </p>
      </div>
      <LifecycleCounts impact={impact} />
      <div>
        <p className="font-semibold">После восстановления</p>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-foreground-secondary">
          <li>Доступ {impact.active_assignment_count} назначенных сотрудников возобновится.</li>
          <li>Кассы нужно проверить и включить отдельно перед открытием смены.</li>
          <li>Edge-узлы, привязанные к кассам, заработают только после включения этих касс.</li>
        </ul>
      </div>
    </>
  );
}

function LifecycleCounts({ impact }: { impact: BranchLifecycleImpact }): JSX.Element {
  const metrics = [
    ["Активные кассы", impact.active_register_count],
    ["Открытые смены", impact.open_shift_count],
    ["Назначенные сотрудники", impact.active_assignment_count],
    ["Edge-узлы", impact.active_edge_node_count],
  ] as const;
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-md border border-border sm:grid-cols-4">
      {metrics.map(([label, value]) => (
        <div key={label} className="border-border p-3 [&:not(:last-child)]:border-r">
          <span className="block text-xs text-foreground-muted">{label}</span>
          <span className="mt-1 block text-lg font-semibold tabular-nums">{value}</span>
        </div>
      ))}
    </div>
  );
}

function pluralizeShift(count: number): string {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return "смен";
  if (mod10 === 1) return "смену";
  if (mod10 >= 2 && mod10 <= 4) return "смены";
  return "смен";
}

function licenseNeedsAttention(number: string | null, expiresAt: string | null): boolean {
  if (!number || !expiresAt) return true;
  const expires = new Date(`${expiresAt}T23:59:59`);
  if (Number.isNaN(expires.getTime())) return true;
  const attentionLimit = new Date();
  attentionLimit.setDate(attentionLimit.getDate() + 60);
  return expires <= attentionLimit;
}

function renderLicense(number: string | null, expiresAt: string | null): JSX.Element {
  if (!number) {
    return <Badge tone="warning">Не указана</Badge>;
  }
  if (!expiresAt) {
    return (
      <div>
        <span className="block font-medium">{number}</span>
        <span className="text-xs text-foreground-muted">Срок не указан</span>
      </div>
    );
  }

  const expires = new Date(`${expiresAt}T23:59:59`);
  const now = new Date();
  const attentionLimit = new Date();
  attentionLimit.setDate(attentionLimit.getDate() + 60);
  const tone = expires < now ? "danger" : expires <= attentionLimit ? "warning" : "success";
  const status = expires < now ? "Истекла" : expires <= attentionLimit ? "Скоро истекает" : null;

  return (
    <div className="space-y-1">
      <span className="block font-medium">{number}</span>
      <span className="block text-xs text-foreground-muted">
        до {expires.toLocaleDateString("ru-RU")}
      </span>
      {status && <Badge tone={tone}>{status}</Badge>}
    </div>
  );
}
