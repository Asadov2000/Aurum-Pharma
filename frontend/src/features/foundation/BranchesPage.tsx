import { useState } from "react";

import {
  Badge,
  Button,
  ConfirmDialog,
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
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";

import { BranchForm } from "./BranchForm";
import { describeApiError } from "./errors";
import { useBranchesQuery, useDeleteBranch } from "./queries";
import { type Branch, type BranchType } from "./types";

const branchTypeLabel: Record<BranchType, string> = {
  pharmacy: "Аптека",
  pharmacy_post: "Аптечный пункт",
  kiosk: "Киоск",
};

export function BranchesPage(): JSX.Element {
  const { user } = useAuth();
  const canCreate = hasPermission(user, "branches.create");
  const canUpdate = hasPermission(user, "branches.update");
  const canDelete = hasPermission(user, "branches.delete");
  const showActions = canUpdate || canDelete;
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<Branch | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Branch | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const { data, isLoading, error } = useBranchesQuery(includeInactive);
  const deleteMutation = useDeleteBranch();

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
        <h1 className="text-2xl font-semibold text-foreground">Точки</h1>
        {canCreate && <Button onClick={() => setCreating(true)}>+ Новая точка</Button>}
      </div>
      <Switch
        label="Показывать неактивные"
        checked={includeInactive}
        onChange={(e) => setIncludeInactive(e.target.checked)}
      />
      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить список")}
        </p>
      )}
      {isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : !data || data.length === 0 ? (
        <TableEmpty>Пока нет ни одной точки</TableEmpty>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Название</TH>
              <TH>Тип</TH>
              <TH>Адрес</TH>
              <TH>Лицензия</TH>
              <TH>Статус</TH>
              {showActions && <TH className="text-right">Действия</TH>}
            </TR>
          </THead>
          <TBody>
            {data.map((b) => (
              <TR key={b.id}>
                <TD className="font-medium">{b.name}</TD>
                <TD>{branchTypeLabel[b.branch_type]}</TD>
                <TD className="max-w-xs truncate">{b.address ?? "—"}</TD>
                <TD>
                  {b.license_number ?? "—"}
                  {b.license_expires_at && (
                    <span className="ml-2 text-xs text-foreground-muted">
                      до {new Date(b.license_expires_at).toLocaleDateString("ru-RU")}
                    </span>
                  )}
                </TD>
                <TD>
                  {b.is_active ? (
                    <Badge tone="success">Активна</Badge>
                  ) : (
                    <Badge tone="neutral">Неактивна</Badge>
                  )}
                </TD>
                {showActions && (
                  <TD className="text-right">
                    {canUpdate && (
                      <Button variant="ghost" size="sm" onClick={() => setEditing(b)}>
                        Изменить
                      </Button>
                    )}
                    {canDelete && b.is_active && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setDeleteError(null);
                          setPendingDelete(b);
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
          title={editing ? `Редактирование: ${editing.name}` : "Новая точка"}
        >
          <BranchForm
            branch={editing}
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
          title="Деактивировать точку"
          message={
            <>
              Деактивировать точку «{pendingDelete?.name}»?
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
