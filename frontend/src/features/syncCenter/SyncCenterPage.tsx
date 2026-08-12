import { useEffect, useMemo, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
  Modal,
  PageHeader,
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
import { hasPlatformCapability, PLATFORM_CAPABILITIES } from "@/features/auth/platformCapabilities";
import { describeApiError } from "@/lib/errorMessages";

import {
  contactStateLabel,
  formatDateTime,
  formatLag,
  healthLabel,
  healthTone,
  integrityStateLabel,
  integrityTone,
  modeLabel,
} from "./labels";
import { useSyncMonitoringOverview } from "./queries";
import { SyncNodeActionModal } from "./SyncNodeActionModal";
import {
  type SyncMonitoringHealth,
  type SyncMonitoringMode,
  type SyncMonitoringNode,
  type SyncMonitoringSummary,
  type SyncNodeAction,
} from "./types";

const PAGE_SIZE = 25;
const SEARCH_DELAY_MS = 400;
type HealthFilter = "all" | SyncMonitoringHealth;
type ModeFilter = "all" | SyncMonitoringMode;

export function SyncCenterPage(): JSX.Element {
  const { user } = useAuth();
  const canView = hasPlatformCapability(user, PLATFORM_CAPABILITIES.syncView);
  const canManage = hasPlatformCapability(user, PLATFORM_CAPABILITIES.syncManage);
  const preferenceKey = useFilterPreferenceKey("platform-sync");
  const [tenantId, setTenantId] = useState("all");
  const [health, setHealth] = useState<HealthFilter>("all");
  const [mode, setMode] = useState<ModeFilter>("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selectedNode, setSelectedNode] = useState<SyncMonitoringNode | null>(null);
  const [nodeAction, setNodeAction] = useState<SyncNodeAction | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const nextSearch = searchInput.trim();
      if (nextSearch === search) return;
      setSearch(nextSearch);
      setPage(1);
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search, searchInput]);

  const filters = useMemo(
    () => ({
      tenant_id: tenantId === "all" ? undefined : tenantId,
      health: health === "all" ? undefined : health,
      mode: mode === "all" ? undefined : mode,
      q: search || undefined,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }),
    [health, mode, page, search, tenantId],
  );
  const overview = useSyncMonitoringOverview(filters, canView);

  useEffect(() => {
    const total = overview.data?.total;
    if (total === undefined || total === 0) return;
    const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [overview.data?.total, page]);

  if (!canView) {
    return (
      <AccessDeniedCard
        title="Синхронизация"
        message="У вас нет доступа к состоянию синхронизации платформы."
        fallbackTo="/admin"
        fallbackLabel="Центр управления"
      />
    );
  }

  const resetFilters = () => {
    setTenantId("all");
    setHealth("all");
    setMode("all");
    setSearchInput("");
    setSearch("");
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Синхронизация"
        description="Состояние Edge-узлов и целостность данных аптек"
        meta={
          overview.data ? <>данные на {formatDateTime(overview.data.generated_at)}</> : undefined
        }
        actions={
          <Button
            variant="secondary"
            size="sm"
            aria-label="Обновить состояние синхронизации"
            title="Обновить состояние синхронизации"
            isLoading={overview.isFetching}
            onClick={() => void overview.refetch()}
          >
            <span aria-hidden="true" className="text-base leading-none">
              ↻
            </span>
            <span>Обновить</span>
          </Button>
        }
      />

      {overview.data && <Summary summary={overview.data.summary} />}

      <ConfigurableFilterBar
        preferenceKey={preferenceKey}
        onResetValues={resetFilters}
        filters={[
          {
            id: "search",
            label: "Поиск",
            alwaysVisible: true,
            active: Boolean(searchInput),
            onClear: () => setSearchInput(""),
            content: (
              <div>
                <Label htmlFor="sync-search">Поиск</Label>
                <Input
                  id="sync-search"
                  type="search"
                  placeholder="Узел, аптека, филиал или касса"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  className="w-full"
                />
              </div>
            ),
          },
          {
            id: "tenant",
            label: "Аптека",
            defaultVisible: true,
            active: tenantId !== "all",
            onClear: () => {
              setTenantId("all");
              setPage(1);
            },
            content: (
              <div>
                <Label htmlFor="sync-tenant">Аптека</Label>
                <Select
                  id="sync-tenant"
                  value={tenantId}
                  onChange={(event) => {
                    setTenantId(event.target.value);
                    setPage(1);
                  }}
                  className="w-full sm:w-64"
                >
                  <option value="all">Все аптеки</option>
                  {overview.data?.tenants.map((tenant) => (
                    <option key={tenant.tenant_id} value={tenant.tenant_id}>
                      {tenant.tenant_name} ({tenant.node_count})
                    </option>
                  ))}
                </Select>
              </div>
            ),
          },
          {
            id: "health",
            label: "Состояние",
            defaultVisible: true,
            active: health !== "all",
            onClear: () => {
              setHealth("all");
              setPage(1);
            },
            content: (
              <div>
                <Label htmlFor="sync-health">Состояние</Label>
                <Select
                  id="sync-health"
                  value={health}
                  onChange={(event) => {
                    setHealth(event.target.value as HealthFilter);
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                >
                  <option value="all">Все состояния</option>
                  <option value="healthy">Стабильно</option>
                  <option value="delayed">Есть задержка</option>
                  <option value="offline">Нет связи</option>
                  <option value="critical">Требует вмешательства</option>
                  <option value="revoked">Отозван</option>
                </Select>
              </div>
            ),
          },
          {
            id: "mode",
            label: "Режим",
            active: mode !== "all",
            onClear: () => {
              setMode("all");
              setPage(1);
            },
            content: (
              <div>
                <Label htmlFor="sync-mode">Режим</Label>
                <Select
                  id="sync-mode"
                  value={mode}
                  onChange={(event) => {
                    setMode(event.target.value as ModeFilter);
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                >
                  <option value="all">Все режимы</option>
                  <option value="edge_writer">Локальная запись</option>
                  <option value="shadow_readonly">Резервное чтение</option>
                </Select>
              </div>
            ),
          },
        ]}
      />

      {overview.isLoading && <SkeletonRows rows={7} />}

      {overview.error && (
        <div role="alert" className="rounded-md border border-danger/30 bg-danger-subtle p-4">
          <p className="text-sm text-danger-foreground">
            {describeApiError(overview.error, "Не удалось загрузить состояние синхронизации.")}
          </p>
          <Button
            className="mt-3"
            size="sm"
            variant="secondary"
            onClick={() => void overview.refetch()}
          >
            Повторить
          </Button>
        </div>
      )}

      {!overview.isLoading && !overview.error && overview.data?.items.length === 0 && (
        <TableEmpty title="Узлы не найдены">
          {overview.data.summary.total_nodes === 0
            ? "Edge-узлы ещё не зарегистрированы."
            : "Измените или сбросьте фильтры."}
        </TableEmpty>
      )}

      {!overview.isLoading && !overview.error && (overview.data?.items.length ?? 0) > 0 && (
        <>
          <NodeTable nodes={overview.data?.items ?? []} onSelect={setSelectedNode} />
          <MobileNodeList nodes={overview.data?.items ?? []} onSelect={setSelectedNode} />
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={overview.data?.total ?? 0}
            onPage={setPage}
          />
        </>
      )}

      <NodeDetailModal
        node={nodeAction ? null : selectedNode}
        canManage={canManage}
        onAction={setNodeAction}
        onClose={() => setSelectedNode(null)}
      />
      {nodeAction && selectedNode && (
        <SyncNodeActionModal
          action={nodeAction}
          node={selectedNode}
          onCompleted={() => void overview.refetch()}
          onClose={() => {
            setNodeAction(null);
            setSelectedNode(null);
          }}
        />
      )}
    </div>
  );
}

function Summary({ summary }: { summary: SyncMonitoringSummary }): JSX.Element {
  const metrics = [
    { label: "Всего узлов", value: summary.total_nodes, tone: "text-foreground" },
    { label: "Стабильно", value: summary.healthy_nodes, tone: "text-success-foreground" },
    { label: "С задержкой", value: summary.delayed_nodes, tone: "text-warning-foreground" },
    { label: "Нет связи", value: summary.offline_nodes, tone: "text-danger" },
    { label: "Критично", value: summary.critical_nodes, tone: "text-danger" },
    { label: "Отозваны", value: summary.revoked_nodes, tone: "text-foreground-muted" },
  ];

  return (
    <section
      aria-label="Сводка синхронизации"
      className="rounded-lg border border-border bg-surface"
    >
      <div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-3 lg:grid-cols-6 lg:divide-y-0">
        {metrics.map((metric) => (
          <div key={metric.label} className="min-w-0 px-3 py-3 sm:px-4">
            <p className="truncate text-xs text-foreground-muted">{metric.label}</p>
            <p className={`mt-1 text-xl font-semibold tabular-nums ${metric.tone}`}>
              {metric.value}
            </p>
          </div>
        ))}
      </div>
      {(summary.never_connected_nodes > 0 ||
        summary.expiring_credentials > 0 ||
        summary.pending_handovers > 0 ||
        summary.pending_credential_rotations > 0) && (
        <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-border px-3 py-2 text-xs text-foreground-secondary sm:px-4">
          <span>Не подключались: {summary.never_connected_nodes}</span>
          <span>Истекли или скоро истекут ключи: {summary.expiring_credentials}</span>
          <span>Ожидают переключения: {summary.pending_handovers}</span>
          <span>Замен ключа в работе: {summary.pending_credential_rotations}</span>
        </div>
      )}
    </section>
  );
}

function NodeTable({
  nodes,
  onSelect,
}: {
  nodes: SyncMonitoringNode[];
  onSelect: (node: SyncMonitoringNode) => void;
}): JSX.Element {
  return (
    <div className="hidden md:block">
      <Table aria-label="Состояние узлов синхронизации">
        <THead>
          <TR>
            <TH>Аптека и филиал</TH>
            <TH>Узел</TH>
            <TH>Состояние</TH>
            <TH>Последнее обращение</TH>
            <TH>Отставание</TH>
            <TH className="w-24 text-right">Подробнее</TH>
          </TR>
        </THead>
        <TBody>
          {nodes.map((node) => (
            <TR key={node.node_id}>
              <TD>
                <p className="max-w-64 truncate font-medium">{node.tenant_name}</p>
                <p className="max-w-64 truncate text-xs text-foreground-muted">
                  {node.branch_name}
                </p>
              </TD>
              <TD>
                <p className="max-w-56 truncate font-medium">{node.display_name}</p>
                <p className="text-xs text-foreground-muted">{modeLabel[node.mode]}</p>
              </TD>
              <TD>
                <div className="space-y-1">
                  <Badge tone={healthTone[node.health]}>{healthLabel[node.health]}</Badge>
                  <p className="text-xs text-foreground-muted">
                    {integrityStateLabel[node.integrity_state]}
                  </p>
                </div>
              </TD>
              <TD className="whitespace-nowrap">
                <p>{formatDateTime(node.last_seen_at)}</p>
                <p className="text-xs text-foreground-muted">
                  {contactStateLabel[node.contact_state]}
                </p>
              </TD>
              <TD className="whitespace-nowrap">{formatLag(node.lag_events)}</TD>
              <TD className="text-right">
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Подробнее об узле ${node.display_name}`}
                  onClick={() => onSelect(node)}
                >
                  Открыть
                </Button>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function MobileNodeList({
  nodes,
  onSelect,
}: {
  nodes: SyncMonitoringNode[];
  onSelect: (node: SyncMonitoringNode) => void;
}): JSX.Element {
  return (
    <div className="space-y-2 md:hidden" aria-label="Состояние узлов синхронизации">
      {nodes.map((node) => (
        <button
          key={node.node_id}
          type="button"
          onClick={() => onSelect(node)}
          className="w-full rounded-lg border border-border bg-surface px-4 py-3 text-left transition-colors duration-fast hover:bg-foreground/[0.025] focus-visible:ring-2 focus-visible:ring-primary"
        >
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate font-medium text-foreground">{node.display_name}</p>
              <p className="truncate text-xs text-foreground-muted">
                {node.tenant_name} · {node.branch_name}
              </p>
            </div>
            <Badge tone={healthTone[node.health]} className="shrink-0">
              {healthLabel[node.health]}
            </Badge>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
            <div>
              <p className="text-foreground-muted">Последнее обращение</p>
              <p className="mt-0.5 text-foreground">{formatDateTime(node.last_seen_at)}</p>
            </div>
            <div>
              <p className="text-foreground-muted">Отставание</p>
              <p className="mt-0.5 text-foreground">{formatLag(node.lag_events)}</p>
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}

function NodeDetailModal({
  node,
  canManage,
  onAction,
  onClose,
}: {
  node: SyncMonitoringNode | null;
  canManage: boolean;
  onAction: (action: SyncNodeAction) => void;
  onClose: () => void;
}): JSX.Element {
  return (
    <Modal
      open={node !== null}
      onClose={onClose}
      title={node ? `Узел: ${node.display_name}` : "Узел синхронизации"}
      className="max-w-2xl"
    >
      {node && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={healthTone[node.health]}>{healthLabel[node.health]}</Badge>
            <Badge tone={integrityTone[node.integrity_state]}>
              Целостность: {integrityStateLabel[node.integrity_state]}
            </Badge>
          </div>

          <div className="grid grid-cols-1 gap-x-6 gap-y-4 text-sm sm:grid-cols-2">
            <DetailField label="Аптека" value={node.tenant_name} />
            <DetailField label="Филиал" value={node.branch_name} />
            <DetailField label="Касса" value={node.register_name ?? "Не привязана"} />
            <DetailField label="Режим" value={modeLabel[node.mode]} />
            <DetailField
              label="Состояние обращения"
              value={contactStateLabel[node.contact_state]}
            />
            <DetailField label="Последнее обращение" value={formatDateTime(node.last_seen_at)} />
            <DetailField label="Последний отчёт" value={formatDateTime(node.latest_report_at)} />
            <DetailField
              label="Результат отчёта"
              value={
                node.latest_report_status === null
                  ? "Нет данных"
                  : node.latest_report_status === "matched"
                    ? "Совпадает"
                    : "Обнаружено расхождение"
              }
            />
            <DetailField
              label="Источник отчёта"
              value={
                node.source_verified === null
                  ? "Не проверен"
                  : node.source_verified
                    ? "Подтверждён"
                    : "Не подтверждён"
              }
            />
            <DetailField label="Отставание" value={formatLag(node.lag_events)} />
            <DetailField label="Текущая последовательность" value={String(node.current_sequence)} />
            <DetailField
              label="Сообщённая последовательность"
              value={
                node.reported_sequence === null ? "Нет данных" : String(node.reported_sequence)
              }
            />
            <DetailField label="Эпоха записи" value={String(node.writer_epoch)} />
            <DetailField
              label="Ключ доступа действует до"
              value={formatDateTime(node.credential_expires_at)}
            />
            <DetailField label="Версия безопасности" value={String(node.lifecycle_version)} />
            {node.credential_rotation_status && (
              <DetailField
                label="Замена ключа"
                value={credentialRotationLabel(node.credential_rotation_status)}
              />
            )}
            {node.credential_rotation_activate_before && (
              <DetailField
                label="Установить новый ключ до"
                value={formatDateTime(node.credential_rotation_activate_before)}
              />
            )}
          </div>

          <div className="rounded-md border border-border bg-foreground/[0.025] px-3 py-2 text-xs text-foreground-muted">
            ID узла:{" "}
            <span className="break-all font-mono text-foreground-secondary">{node.node_id}</span>
          </div>

          <div className="flex flex-wrap justify-end gap-2">
            {canManage && node.node_status === "active" && node.mode === "shadow_readonly" && (
              <>
                {node.credential_rotation_status === null && (
                  <Button variant="secondary" onClick={() => onAction("rotate")}>
                    Заменить ключ
                  </Button>
                )}
                {node.credential_rotation_status === "verified" && (
                  <Button onClick={() => onAction("complete")}>Завершить замену</Button>
                )}
                {node.credential_rotation_status !== null && (
                  <Button variant="secondary" onClick={() => onAction("cancel")}>
                    Отменить замену
                  </Button>
                )}
                <Button variant="danger" onClick={() => onAction("revoke")}>
                  Отозвать узел
                </Button>
              </>
            )}
            <Button variant="secondary" onClick={onClose}>
              Закрыть
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function credentialRotationLabel(status: "pending" | "verified" | "expired"): string {
  if (status === "verified") return "Новый ключ подтверждён";
  if (status === "expired") return "Срок установки истёк";
  return "Ожидается подключение нового ключа";
}

function DetailField({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="min-w-0">
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className="mt-0.5 break-words font-medium text-foreground">{value}</p>
    </div>
  );
}

export default SyncCenterPage;
