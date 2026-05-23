import { useState } from "react";

import {
  Badge,
  Button,
  Modal,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { AdminBillingDrawer } from "@/features/billing/AdminBillingDrawer";

import { describeApiError } from "./errors";
import { useTenantsQuery } from "./queries";
import { TenantForm } from "./TenantForm";
import { type Tenant, type TenantStatus } from "./types";

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
  const [editing, setEditing] = useState<Tenant | null>(null);
  const [creating, setCreating] = useState(false);
  const [billingTenant, setBillingTenant] = useState<Tenant | null>(null);
  const { data, isLoading, error } = useTenantsQuery();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Тенанты</h1>
        <Button onClick={() => setCreating(true)}>+ Новый тенант</Button>
      </div>
      {error && (
        <p className="text-sm text-red-600">
          {describeApiError(error, "Не удалось загрузить список")}
        </p>
      )}
      {isLoading ? (
        <p className="text-sm text-slate-500">Загрузка…</p>
      ) : !data || data.length === 0 ? (
        <TableEmpty>Пока нет ни одного тенанта</TableEmpty>
      ) : (
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
            {data.map((t) => (
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
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <Modal
        open={creating || editing !== null}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
        title={editing ? `Редактирование: ${editing.name}` : "Новый тенант"}
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
    </div>
  );
}
