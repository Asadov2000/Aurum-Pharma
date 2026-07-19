import { useState } from "react";

import {
  Badge,
  Button,
  ConfirmDialog,
  Label,
  Modal,
  Select,
  Switch,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";

import { describeApiError } from "./errors";
import { useBranchesQuery, useDeleteRegister, useRegistersQuery } from "./queries";
import { RegisterForm } from "./RegisterForm";
import { type PrinterType, type Register } from "./types";

const printerLabel: Record<PrinterType, string> = {
  browser: "Браузер",
  thermal_58: "58 мм",
  thermal_80: "80 мм",
  a4: "A4",
};

export function RegistersPage(): JSX.Element {
  const { user } = useAuth();
  const canCreate = hasPermission(user, "registers.create");
  const canUpdate = hasPermission(user, "registers.update");
  const canDelete = hasPermission(user, "registers.delete");
  const showActions = canUpdate || canDelete;
  const [branchFilter, setBranchFilter] = useState<string>("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<Register | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Register | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const branches = useBranchesQuery(true);
  const { data, isLoading, error } = useRegistersQuery(branchFilter || null, includeInactive);
  const deleteMutation = useDeleteRegister();

  const branchNameById = (id: string): string =>
    branches.data?.find((b) => b.id === id)?.name ?? id.slice(0, 8);

  const confirmDelete = async () => {
    if (!pendingDelete || !canDelete) return;
    setDeleteError(null);
    try {
      await deleteMutation.mutateAsync(pendingDelete.id);
      setPendingDelete(null);
    } catch (err) {
      setDeleteError(describeApiError(err, "Не удалось деактивировать"));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Кассы</h1>
        {canCreate && <Button onClick={() => setCreating(true)}>+ Новая касса</Button>}
      </div>
      <div className="flex items-end gap-4">
        <div>
          <Label htmlFor="branch_filter">Точка</Label>
          <Select
            id="branch_filter"
            value={branchFilter}
            onChange={(e) => setBranchFilter(e.target.value)}
            className="w-64"
          >
            <option value="">Все точки</option>
            {branches.data?.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </Select>
        </div>
        <Switch
          label="Показывать неактивные"
          checked={includeInactive}
          onChange={(e) => setIncludeInactive(e.target.checked)}
        />
      </div>
      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить список")}
        </p>
      )}
      {isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : !data || data.length === 0 ? (
        <TableEmpty>Пока нет ни одной кассы</TableEmpty>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Название</TH>
              <TH>Точка</TH>
              <TH>Принтер</TH>
              <TH>Статус</TH>
              {showActions && <TH className="text-right">Действия</TH>}
            </TR>
          </THead>
          <TBody>
            {data.map((r) => (
              <TR key={r.id}>
                <TD className="font-medium">{r.name}</TD>
                <TD>{branchNameById(r.branch_id)}</TD>
                <TD>{r.printer_type ? printerLabel[r.printer_type] : "—"}</TD>
                <TD>
                  {r.is_active ? (
                    <Badge tone="success">Активна</Badge>
                  ) : (
                    <Badge tone="neutral">Неактивна</Badge>
                  )}
                </TD>
                {showActions && (
                  <TD className="text-right">
                    {canUpdate && (
                      <Button variant="ghost" size="sm" onClick={() => setEditing(r)}>
                        Изменить
                      </Button>
                    )}
                    {canDelete && r.is_active && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setDeleteError(null);
                          setPendingDelete(r);
                        }}
                        isLoading={deleteMutation.isPending}
                      >
                        Удалить
                      </Button>
                    )}
                  </TD>
                )}
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      {(canCreate || canUpdate) && (
        <Modal
          open={creating || editing !== null}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          title={editing ? `Редактирование: ${editing.name}` : "Новая касса"}
        >
          <RegisterForm
            register={editing}
            onClose={() => {
              setCreating(false);
              setEditing(null);
            }}
          />
        </Modal>
      )}
      {canDelete && (
        <ConfirmDialog
          open={pendingDelete !== null}
          title="Деактивировать кассу"
          message={
            <>
              Деактивировать кассу «{pendingDelete?.name}»?
              {deleteError && <span className="mt-2 block text-danger">{deleteError}</span>}
            </>
          }
          confirmLabel="Деактивировать"
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => void confirmDelete()}
          onCancel={() => {
            setPendingDelete(null);
            setDeleteError(null);
          }}
        />
      )}
    </div>
  );
}
