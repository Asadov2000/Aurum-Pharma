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
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<Branch | null>(null);
  const [creating, setCreating] = useState(false);
  const { data, isLoading, error } = useBranchesQuery(includeInactive);
  const deleteMutation = useDeleteBranch();

  const handleDelete = async (b: Branch) => {
    if (!window.confirm(`Деактивировать точку «${b.name}»?`)) return;
    try {
      await deleteMutation.mutateAsync(b.id);
    } catch (err) {
      window.alert(describeApiError(err, "Не удалось деактивировать"));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Точки</h1>
        <Button onClick={() => setCreating(true)}>+ Новая точка</Button>
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
              <TH className="text-right">Действия</TH>
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
                <TD className="text-right">
                  <Button variant="ghost" size="sm" onClick={() => setEditing(b)}>
                    Изменить
                  </Button>
                  {b.is_active && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void handleDelete(b)}
                      isLoading={deleteMutation.isPending}
                    >
                      Удалить
                    </Button>
                  )}
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
    </div>
  );
}
