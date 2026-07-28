import { useState } from "react";

import {
  Badge,
  ConfigurableFilterBar,
  Input,
  Label,
  Pagination,
  Select,
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
import { describeApiError } from "@/features/foundation/errors";

import { AuditEntryModal } from "./AuditEntryModal";
import { actionLabel, actionTone, tableLabel } from "./labels";
import { useAuditQuery } from "./queries";
import { type AuditEntry, type AuditScope } from "./types";

const PAGE_SIZE = 50;

const scopeLabel: Record<AuditScope, string> = {
  my: "Мои действия",
  tenant: "Все по тенанту",
  global: "Глобально",
};

export function AuditPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("audit");
  const canViewGlobalAudit = Boolean(user?.is_developer);
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

  const { data, isLoading, error } = useAuditQuery({
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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Журнал аудита</h1>
        <span className="text-sm text-foreground-muted">всего: {total}</span>
      </div>

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
                  className="w-52"
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
                <Input
                  id="action"
                  placeholder="insert / update / …"
                  value={action}
                  onChange={(e) => {
                    setAction(e.target.value);
                    setPage(1);
                  }}
                  className="w-44"
                />
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
                <Label htmlFor="table_name">Таблица</Label>
                <Input
                  id="table_name"
                  placeholder="batch / sale / …"
                  value={tableName}
                  onChange={(e) => {
                    setTableName(e.target.value);
                    setPage(1);
                  }}
                  className="w-44"
                />
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
                <Label htmlFor="user_id">User ID</Label>
                <Input
                  id="user_id"
                  placeholder="UUID"
                  value={userId}
                  onChange={(e) => {
                    setUserId(e.target.value);
                    setPage(1);
                  }}
                  className="w-44"
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
                <Label htmlFor="tenant_id">Tenant ID</Label>
                <Input
                  id="tenant_id"
                  placeholder="UUID"
                  value={tenantId}
                  onChange={(e) => {
                    setTenantId(e.target.value);
                    setPage(1);
                  }}
                  className="w-44"
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
              <div className="grid w-64 grid-cols-1 gap-2 sm:w-auto sm:grid-cols-2">
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
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить журнал")}
        </p>
      )}

      {isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : !data || data.items.length === 0 ? (
        <TableEmpty>
          {action || tableName || userId || tenantId || dateFrom || dateTo
            ? "По текущим фильтрам ничего не найдено"
            : "События пока не записаны"}
        </TableEmpty>
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Когда</TH>
                <TH>Действие</TH>
                <TH>Таблица</TH>
                <TH>Запись</TH>
                <TH>Пользователь</TH>
                <TH>IP</TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((e) => (
                <TR key={e.id} className="cursor-pointer" onClick={() => setOpened(e)}>
                  <TD className="whitespace-nowrap">
                    {new Date(e.created_at).toLocaleString("ru-RU")}
                  </TD>
                  <TD>
                    <Badge tone={actionTone(e.action)}>{actionLabel[e.action] ?? e.action}</Badge>
                  </TD>
                  <TD>{tableLabel[e.table_name] ?? e.table_name}</TD>
                  <TD className="font-mono text-xs">
                    {e.record_id ? e.record_id.slice(0, 8) : "—"}
                  </TD>
                  <TD className="font-mono text-xs">{e.user_id ? e.user_id.slice(0, 8) : "—"}</TD>
                  <TD className="font-mono text-xs">{e.ip_address ?? "—"}</TD>
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
