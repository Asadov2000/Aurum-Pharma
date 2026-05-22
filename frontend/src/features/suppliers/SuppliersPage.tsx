import { useState } from "react";

import {
  Badge,
  Button,
  Modal,
  Switch,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { useSuppliersQuery } from "./queries";
import { SupplierForm } from "./SupplierForm";
import { type Supplier } from "./types";

export function SuppliersPage(): JSX.Element {
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [creating, setCreating] = useState(false);
  const { data, isLoading, error } = useSuppliersQuery(includeInactive);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Поставщики</h1>
        <Button onClick={() => setCreating(true)}>+ Новый поставщик</Button>
      </div>
      <Switch
        label="Показывать неактивных"
        checked={includeInactive}
        onChange={(e) => setIncludeInactive(e.target.checked)}
      />
      {error && (
        <p className="text-sm text-red-600">
          {describeApiError(error, "Не удалось загрузить список")}
        </p>
      )}
      {isLoading ? (
        <p className="text-sm text-slate-500">Загрузка…</p>
      ) : !data || data.length === 0 ? (
        <TableEmpty>Поставщиков пока нет</TableEmpty>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Название</TH>
              <TH>Контакт</TH>
              <TH>Телефон</TH>
              <TH>Email</TH>
              <TH>Статус</TH>
              <TH className="text-right">Действия</TH>
            </TR>
          </THead>
          <TBody>
            {data.map((s) => (
              <TR key={s.id}>
                <TD className="font-medium">{s.name}</TD>
                <TD>{s.contact_person ?? "—"}</TD>
                <TD>{s.phone ?? "—"}</TD>
                <TD>{s.email ?? "—"}</TD>
                <TD>
                  {s.is_active ? (
                    <Badge tone="success">Активен</Badge>
                  ) : (
                    <Badge tone="neutral">Неактивен</Badge>
                  )}
                </TD>
                <TD className="text-right">
                  <Button variant="ghost" size="sm" onClick={() => setEditing(s)}>
                    Изменить
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
        title={editing ? `Редактирование: ${editing.name}` : "Новый поставщик"}
      >
        <SupplierForm
          supplier={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      </Modal>
    </div>
  );
}
