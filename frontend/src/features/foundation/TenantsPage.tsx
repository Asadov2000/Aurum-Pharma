import { useMemo, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  ActionMenu,
  Badge,
  Button,
  FilterBar,
  Input,
  Label,
  Modal,
  PageHeader,
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
import { useAuth } from "@/features/auth/hooks";
import { hasPlatformCapability, PLATFORM_CAPABILITIES } from "@/features/auth/platformCapabilities";
import { SupportAccessForm } from "@/features/supportAccess/SupportAccessForm";

import { describeApiError } from "./errors";
import { useTenantsQuery } from "./queries";
import { TenantForm } from "./TenantForm";
import { TenantMemberForm } from "./TenantMemberForm";
import { type Tenant, type TenantStatus } from "./types";

const PAGE_SIZE = 20;

const statusTone: Record<TenantStatus, "neutral" | "success" | "warning" | "danger" | "info"> = {
  setup: "neutral",
  trial: "info",
  active: "success",
  grace_period: "warning",
  readonly: "warning",
  archived: "danger",
};

const statusLabel: Record<TenantStatus, string> = {
  setup: "Настройка",
  trial: "Пробный",
  active: "Активен",
  grace_period: "Льготный",
  readonly: "Только чтение",
  archived: "Архив",
};

export function TenantsPage(): JSX.Element {
  const { user } = useAuth();
  const canViewTenants = hasPlatformCapability(user, PLATFORM_CAPABILITIES.tenantsView);
  const canManageTenants = hasPlatformCapability(user, PLATFORM_CAPABILITIES.tenantsManage);
  const canCreateOwner = hasPlatformCapability(user, PLATFORM_CAPABILITIES.ownershipProvision);
  const canManageMembers = hasPlatformCapability(user, PLATFORM_CAPABILITIES.membershipsManage);
  const canManageBilling = hasPlatformCapability(user, PLATFORM_CAPABILITIES.billingManage);
  const canUseSupport = hasPlatformCapability(user, PLATFORM_CAPABILITIES.supportUse);
  const canCreateTenant = canManageTenants && canCreateOwner;
  const hasTenantActions = canManageTenants || canManageMembers || canUseSupport;
  const [editing, setEditing] = useState<Tenant | null>(null);
  const [creating, setCreating] = useState(false);
  const [memberTenant, setMemberTenant] = useState<Tenant | null>(null);
  const [supportTenant, setSupportTenant] = useState<Tenant | null>(null);
  const [supportRequestPending, setSupportRequestPending] = useState(false);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useTenantsQuery(canViewTenants);

  // Search + pagination are client-side: the API has no search param and the
  // tenant count is small (we already fetch up to 500).
  const filtered = useMemo(() => {
    const all = data ?? [];
    const needle = q.trim().toLowerCase();
    if (!needle) return all;
    return all.filter(
      (t) =>
        t.name.toLowerCase().includes(needle) || t.contact_email.toLowerCase().includes(needle),
    );
  }, [data, q]);

  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  if (!canViewTenants) {
    return (
      <AccessDeniedCard title="Аптеки" message="У вас нет доступа к администрированию аптек." />
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Аптеки"
        actions={
          canCreateTenant ? (
            <Button onClick={() => setCreating(true)}>+ Новая аптека</Button>
          ) : undefined
        }
      />

      {data && data.length > 0 && (
        <FilterBar>
          <div className="flex-1">
            <Label htmlFor="tenant-q">Поиск (название или email)</Label>
            <Input
              id="tenant-q"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
              placeholder="например: Шифо"
            />
          </div>
        </FilterBar>
      )}

      {error ? (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm leading-5 text-danger-foreground"
        >
          {describeApiError(error, "Не удалось загрузить список")}
        </div>
      ) : isLoading ? (
        <SkeletonRows rows={6} />
      ) : !data || data.length === 0 ? (
        <TableEmpty>Пока нет ни одной аптеки</TableEmpty>
      ) : filtered.length === 0 ? (
        <TableEmpty title="Ничего не найдено">Измените поисковый запрос.</TableEmpty>
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Название</TH>
                <TH>Email</TH>
                <TH>Статус</TH>
                <TH>Создан</TH>
                {hasTenantActions && <TH className="text-right">Действия</TH>}
              </TR>
            </THead>
            <TBody>
              {paged.map((t) => (
                <TR key={t.id}>
                  <TD className="font-medium">{t.name}</TD>
                  <TD>{t.contact_email}</TD>
                  <TD>
                    <Badge tone={statusTone[t.status]}>{statusLabel[t.status]}</Badge>
                  </TD>
                  <TD>{new Date(t.created_at).toLocaleDateString("ru-RU")}</TD>
                  {hasTenantActions && (
                    <TD className="w-12 text-right">
                      <ActionMenu
                        label={`Действия для ${t.name}`}
                        items={[
                          ...(canManageTenants
                            ? [{ label: "Изменить", onSelect: () => setEditing(t) }]
                            : []),
                          ...(t.status !== "archived" && canUseSupport
                            ? [
                                {
                                  label: "Открыть защищённый доступ",
                                  onSelect: () => {
                                    setSupportRequestPending(false);
                                    setSupportTenant(t);
                                  },
                                },
                              ]
                            : []),
                          ...(t.status !== "archived" && canManageMembers
                            ? [
                                {
                                  label: "Добавить сотрудника",
                                  onSelect: () => setMemberTenant(t),
                                },
                              ]
                            : []),
                        ]}
                      />
                    </TD>
                  )}
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPage={setPage} />
        </>
      )}

      <Modal
        open={creating || editing !== null}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
        title={editing ? `Редактирование: ${editing.name}` : "Новая аптека"}
      >
        <TenantForm
          tenant={editing}
          canManageStatus={canManageBilling}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      </Modal>
      <Modal
        open={memberTenant !== null}
        onClose={() => setMemberTenant(null)}
        title="Новый сотрудник"
      >
        {memberTenant && (
          <TenantMemberForm
            tenantId={memberTenant.id}
            tenantName={memberTenant.name}
            onClose={() => setMemberTenant(null)}
          />
        )}
      </Modal>
      <Modal
        open={supportTenant !== null}
        onClose={() => {
          if (!supportRequestPending) setSupportTenant(null);
        }}
        title="Защищённый доступ"
      >
        {supportTenant && (
          <SupportAccessForm
            tenantId={supportTenant.id}
            tenantName={supportTenant.name}
            onClose={() => setSupportTenant(null)}
            onPendingChange={setSupportRequestPending}
          />
        )}
      </Modal>
    </div>
  );
}
