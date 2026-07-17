import { useMemo, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  Badge,
  Button,
  FilterBar,
  Input,
  Label,
  Modal,
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
import { AdminBillingDrawer } from "@/features/billing/AdminBillingDrawer";

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
  const canManageTenants = Boolean(user?.is_developer || user?.is_administrator);
  const [editing, setEditing] = useState<Tenant | null>(null);
  const [creating, setCreating] = useState(false);
  const [billingTenant, setBillingTenant] = useState<Tenant | null>(null);
  const [memberTenant, setMemberTenant] = useState<Tenant | null>(null);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useTenantsQuery(canManageTenants);

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

  if (!canManageTenants) {
    return (
      <AccessDeniedCard title="Аптеки" message="У вас нет доступа к администрированию аптек." />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Тенанты</h1>
        <Button onClick={() => setCreating(true)}>+ Новая аптека</Button>
      </div>

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
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить список")}
        </p>
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
                <TH className="text-right">Действия</TH>
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
                  <TD className="text-right whitespace-nowrap">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(t)}>
                      Изменить
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setBillingTenant(t)}>
                      Биллинг
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={t.status === "archived"}
                      title={
                        t.status === "archived"
                          ? "В архивную аптеку нельзя добавить сотрудника"
                          : undefined
                      }
                      onClick={() => setMemberTenant(t)}
                    >
                      Добавить сотрудника
                    </Button>
                  </TD>
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
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      </Modal>
      {billingTenant && (
        <AdminBillingDrawer
          tenantId={billingTenant.id}
          tenantName={billingTenant.name}
          onClose={() => setBillingTenant(null)}
        />
      )}
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
    </div>
  );
}
