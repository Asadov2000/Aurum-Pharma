import { type KeyboardEvent, useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
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
import { describeApiError } from "@/features/foundation/errors";

import { AuditEntryModal } from "./AuditEntryModal";
import { actionLabel, actionTone, tableLabel } from "./labels";
import { useAuditQuery } from "./queries";
import { type AuditEntry, type AuditScope } from "./types";

const PAGE_SIZE = 50;

const WRITE_ACTIONS = new Set(["INSERT", "UPDATE", "DELETE"]);
const ATTENTION_ACTIONS = new Set(["DELETE", "IMPERSONATE", "ROLE_REVOKE"]);

const actionOptions = [
  "INSERT",
  "UPDATE",
  "DELETE",
  "VIEW",
  "EXPORT",
  "IMPERSONATE",
  "MEMBERSHIP_CREATED",
  "MEMBERSHIP_ACTIVATED",
  "OWNERSHIP_GRANTED",
  "ROLE_PERMISSIONS_CHANGED",
] as const;

const scopeLabel: Record<AuditScope, string> = {
  my: "Мои действия",
  tenant: "Вся аптека",
  global: "Вся платформа",
};

export function AuditPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("audit");
  const canViewGlobalAudit = hasPlatformCapability(user, PLATFORM_CAPABILITIES.auditGlobalView);
  const defaultScope: AuditScope = canViewGlobalAudit && !user?.home_tenant_id ? "global" : "my";

  const [scope, setScope] = useState<AuditScope>(defaultScope);
  const [action, setAction] = useState("");
  const [tableName, setTableName] = useState("");
  const [userId, setUserId] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [opened, setOpened] = useState<AuditEntry | null>(null);

  const { data, isLoading, isFetching, error, refetch } = useAuditQuery({
    scope,
    action: action || undefined,
    table_name: tableName || undefined,
    user_id: userId || undefined,
    tenant_id: tenantId || undefined,
    // Keep date inputs as calendar dates. The API resolves them in the
    // pharmacy timezone; converting with `new Date("YYYY-MM-DD")` would first
    // interpret midnight as UTC and shift the selected day for Tajik users.
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    page,
    page_size: PAGE_SIZE,
  });

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  const hasFilters = Boolean(
    scope !== defaultScope || action || tableName || userId || tenantId || dateFrom || dateTo,
  );
  const writeCount = items.filter((entry) => WRITE_ACTIONS.has(entry.action.toUpperCase())).length;
  const attentionCount = items.filter((entry) =>
    ATTENTION_ACTIONS.has(entry.action.toUpperCase()),
  ).length;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Журнал аудита"
        description="История действий пользователей и изменений критичных данных аптеки."
        meta={<>Найдено: {total}</>}
        showTitleOnDesktop
      />

      <AuditSummary
        total={total}
        visible={items.length}
        writeCount={writeCount}
        attentionCount={attentionCount}
        loading={isLoading}
      />

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "scope",
            label: "Область",
            content: (
              <div>
                <Label htmlFor="scope">Область</Label>
                <Select
                  id="scope"
                  value={scope}
                  onChange={(e) => {
                    const nextScope = e.target.value as AuditScope;
                    setScope(nextScope);
                    if (nextScope === "my") setUserId("");
                    if (nextScope !== "global") setTenantId("");
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                >
                  <option value="my">{scopeLabel.my}</option>
                  <option value="tenant">{scopeLabel.tenant}</option>
                  {canViewGlobalAudit && <option value="global">{scopeLabel.global}</option>}
                </Select>
              </div>
            ),
            active: scope !== defaultScope,
            onClear: () => {
              setScope(defaultScope);
              setUserId("");
              setTenantId("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "action",
            label: "Действие",
            content: (
              <div>
                <Label htmlFor="action">Действие</Label>
                <Select
                  id="action"
                  value={action}
                  onChange={(e) => {
                    setAction(e.target.value);
                    setPage(1);
                  }}
                  className="w-full sm:w-48"
                >
                  <option value="">Все действия</option>
                  {actionOptions.map((value) => (
                    <option key={value} value={value}>
                      {actionLabel[value] ?? value}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(action),
            onClear: () => {
              setAction("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "table",
            label: "Раздел данных",
            content: (
              <div>
                <Label htmlFor="table_name">Раздел данных</Label>
                <Select
                  id="table_name"
                  value={tableName}
                  onChange={(e) => {
                    setTableName(e.target.value);
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                >
                  <option value="">Все разделы</option>
                  {Object.entries(tableLabel).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(tableName),
            onClear: () => {
              setTableName("");
              setPage(1);
            },
          },
          {
            id: "user",
            label: "Пользователь",
            content: (
              <div>
                <Label htmlFor="user_id">ID пользователя</Label>
                <Input
                  id="user_id"
                  placeholder="UUID"
                  value={userId}
                  onChange={(e) => {
                    setUserId(e.target.value);
                    setPage(1);
                  }}
                  className="w-full sm:w-44"
                />
              </div>
            ),
            active: Boolean(userId),
            onClear: () => {
              setUserId("");
              setPage(1);
            },
            available: scope === "tenant" || scope === "global",
          },
          {
            id: "tenant",
            label: "Аптека",
            content: (
              <div>
                <Label htmlFor="tenant_id">ID аптеки</Label>
                <Input
                  id="tenant_id"
                  placeholder="UUID"
                  value={tenantId}
                  onChange={(e) => {
                    setTenantId(e.target.value);
                    setPage(1);
                  }}
                  className="w-full sm:w-44"
                />
              </div>
            ),
            active: Boolean(tenantId),
            onClear: () => {
              setTenantId("");
              setPage(1);
            },
            available: scope === "global",
          },
          {
            id: "period",
            label: "Период",
            content: (
              <div className="grid w-full grid-cols-1 gap-2 sm:w-auto sm:grid-cols-2">
                <div>
                  <Label htmlFor="date_from">От</Label>
                  <Input
                    id="date_from"
                    type="date"
                    value={dateFrom}
                    max={dateTo || undefined}
                    onChange={(e) => {
                      setDateFrom(e.target.value);
                      setPage(1);
                    }}
                  />
                </div>
                <div>
                  <Label htmlFor="date_to">До</Label>
                  <Input
                    id="date_to"
                    type="date"
                    value={dateTo}
                    min={dateFrom || undefined}
                    onChange={(e) => {
                      setDateTo(e.target.value);
                      setPage(1);
                    }}
                  />
                </div>
              </div>
            ),
            active: Boolean(dateFrom || dateTo),
            onClear: () => {
              setDateFrom("");
              setDateTo("");
              setPage(1);
            },
            defaultVisible: true,
          },
        ]}
        onResetValues={() => {
          setScope(defaultScope);
          setAction("");
          setTableName("");
          setUserId("");
          setTenantId("");
          setDateFrom("");
          setDateTo("");
          setPage(1);
        }}
      />

      {error && (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3"
          role="alert"
        >
          <p className="text-sm text-danger-foreground">
            {describeApiError(error, "Не удалось загрузить журнал")}
          </p>
          <Button
            variant="secondary"
            size="sm"
            isLoading={isFetching}
            onClick={() => void refetch()}
          >
            Повторить
          </Button>
        </div>
      )}

      {isLoading ? (
        <SkeletonRows rows={8} />
      ) : !data || items.length === 0 ? (
        <TableEmpty title={hasFilters ? "События не найдены" : "События пока не записаны"}>
          {hasFilters
            ? "Измените или сбросьте текущие фильтры."
            : "События появятся после действий пользователей."}
        </TableEmpty>
      ) : (
        <>
          <div className="flex min-w-0 flex-wrap items-end justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-foreground">События</h2>
              <p className="mt-0.5 text-xs text-foreground-muted">{scopeLabel[scope]}</p>
            </div>
            {isFetching && !isLoading ? (
              <span className="text-xs text-foreground-muted" role="status">
                Обновление…
              </span>
            ) : null}
          </div>

          <Table aria-label="События журнала аудита">
            <THead>
              <TR>
                <TH>Дата и время</TH>
                <TH>Действие</TH>
                <TH>Раздел данных</TH>
                <TH>Объект</TH>
                <TH>Пользователь</TH>
                <TH>IP</TH>
              </TR>
            </THead>
            <TBody>
              {items.map((entry) => (
                <TR
                  key={entry.id}
                  className="cursor-pointer focus-visible:bg-primary/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                  tabIndex={0}
                  aria-label={`Открыть событие: ${actionLabel[entry.action] ?? entry.action}, ${tableLabel[entry.table_name] ?? entry.table_name}`}
                  onClick={() => setOpened(entry)}
                  onKeyDown={(event) => openEntryFromKeyboard(event, entry, setOpened)}
                >
                  <TD className="whitespace-nowrap">
                    <AuditTimestamp value={entry.created_at} />
                  </TD>
                  <TD>
                    <Badge tone={actionTone(entry.action)}>
                      {actionLabel[entry.action] ?? entry.action}
                    </Badge>
                  </TD>
                  <TD>
                    <span className="font-medium">
                      {tableLabel[entry.table_name] ?? entry.table_name}
                    </span>
                    <span className="mt-0.5 block font-mono text-[11px] text-foreground-muted">
                      {entry.table_name}
                    </span>
                  </TD>
                  <TD className="font-mono text-xs">
                    {entry.record_id ? entry.record_id.slice(0, 8) : "—"}
                  </TD>
                  <TD className="font-mono text-xs">
                    {entry.user_id ? entry.user_id.slice(0, 8) : "Система"}
                  </TD>
                  <TD className="font-mono text-xs text-foreground-secondary">
                    {entry.ip_address ?? "—"}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>

          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
        </>
      )}

      <AuditEntryModal entry={opened} onClose={() => setOpened(null)} />
    </div>
  );
}

function AuditSummary({
  total,
  visible,
  writeCount,
  attentionCount,
  loading,
}: {
  total: number;
  visible: number;
  writeCount: number;
  attentionCount: number;
  loading: boolean;
}): JSX.Element {
  const metrics = [
    { label: "Всего по фильтру", value: total },
    { label: "На странице", value: visible },
    { label: "Изменения на странице", value: writeCount },
    { label: "Требуют внимания", value: attentionCount, danger: attentionCount > 0 },
  ];

  return (
    <section
      className="grid overflow-hidden rounded-lg border border-border bg-surface sm:grid-cols-2 xl:grid-cols-4"
      aria-label="Сводка журнала аудита"
    >
      {metrics.map((metric, index) => (
        <div
          key={metric.label}
          className={`min-w-0 px-5 py-4 ${
            index > 0 ? "border-t border-border sm:border-l" : ""
          } ${index === 1 ? "sm:border-t-0" : ""} ${
            index === 2 ? "sm:border-l-0 xl:border-l" : ""
          } ${index >= 2 ? "xl:border-t-0" : ""}`}
        >
          <p className="text-xs font-medium text-foreground-muted">{metric.label}</p>
          {loading ? (
            <div className="mt-2 h-7 w-16 animate-pulse rounded bg-foreground/10" />
          ) : (
            <p
              className={`mt-1 font-display text-2xl font-semibold tabular-nums ${
                metric.danger ? "text-danger" : "text-foreground"
              }`}
            >
              {metric.value.toLocaleString("ru-RU")}
            </p>
          )}
        </div>
      ))}
    </section>
  );
}

function AuditTimestamp({ value }: { value: string }): JSX.Element {
  const date = new Date(value);
  return (
    <time dateTime={value}>
      <span className="block font-medium">
        {date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" })}
      </span>
      <span className="mt-0.5 block text-xs tabular-nums text-foreground-muted">
        {date.toLocaleTimeString("ru-RU", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })}
      </span>
    </time>
  );
}

function openEntryFromKeyboard(
  event: KeyboardEvent<HTMLTableRowElement>,
  entry: AuditEntry,
  open: (entry: AuditEntry) => void,
): void {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  open(entry);
}
