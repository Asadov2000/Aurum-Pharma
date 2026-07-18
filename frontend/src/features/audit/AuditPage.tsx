import { useState } from "react";

import {
  Badge,
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
  const canViewGlobalAudit = Boolean(user?.is_developer);

  const [scope, setScope] = useState<AuditScope>("my");
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
    date_from: dateFrom ? new Date(dateFrom).toISOString() : undefined,
    date_to: dateTo ? new Date(dateTo).toISOString() : undefined,
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

      <div className="flex flex-wrap items-end gap-3 rounded-md border border-border bg-surface p-3">
        <div>
          <Label htmlFor="scope">Область</Label>
          <Select
            id="scope"
            value={scope}
            onChange={(e) => {
              setScope(e.target.value as AuditScope);
              setPage(1);
            }}
            className="w-52"
          >
            <option value="my">{scopeLabel.my}</option>
            <option value="tenant">{scopeLabel.tenant}</option>
            {canViewGlobalAudit && <option value="global">{scopeLabel.global}</option>}
          </Select>
        </div>
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
        {(scope === "tenant" || scope === "global") && (
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
        )}
        {scope === "global" && (
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
        )}
        <div>
          <Label htmlFor="date_from">От</Label>
          <Input
            id="date_from"
            type="date"
            value={dateFrom}
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
            onChange={(e) => {
              setDateTo(e.target.value);
              setPage(1);
            }}
          />
        </div>
      </div>

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
                <TR
                  key={e.id}
                  className="cursor-pointer"
                  onClick={() => setOpened(e)}
                >
                  <TD className="whitespace-nowrap">
                    {new Date(e.created_at).toLocaleString("ru-RU")}
                  </TD>
                  <TD>
                    <Badge tone={actionTone(e.action)}>
                      {actionLabel[e.action] ?? e.action}
                    </Badge>
                  </TD>
                  <TD>{tableLabel[e.table_name] ?? e.table_name}</TD>
                  <TD className="font-mono text-xs">
                    {e.record_id ? e.record_id.slice(0, 8) : "—"}
                  </TD>
                  <TD className="font-mono text-xs">
                    {e.user_id ? e.user_id.slice(0, 8) : "—"}
                  </TD>
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
