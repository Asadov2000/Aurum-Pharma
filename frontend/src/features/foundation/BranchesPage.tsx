import { useEffect, useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  ConfirmDialog,
  Input,
  Label,
  Modal,
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
import { hasPermission } from "@/features/auth/permissions";

import { BranchForm } from "./BranchForm";
import { describeApiError } from "./errors";
import { useBranchSearchQuery, useDeleteBranch } from "./queries";
import { type Branch, type BranchType } from "./types";

const PAGE_SIZE = 25;

const branchTypeLabel: Record<BranchType, string> = {
  pharmacy: "Аптека",
  pharmacy_post: "Аптечный пункт",
  kiosk: "Киоск",
};

type StatusFilter = "active" | "inactive" | "all";

export function BranchesPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("branches");
  const canCreate = hasPermission(user, "branches.create");
  const canUpdate = hasPermission(user, "branches.update");
  const canDelete = hasPermission(user, "branches.delete");
  const showActions = canUpdate || canDelete;
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [branchType, setBranchType] = useState<BranchType | "">("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Branch | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Branch | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  const { data, isLoading, isFetching, error } = useBranchSearchQuery({
    q,
    branch_type: branchType || undefined,
    is_active: status === "all" ? undefined : status === "active",
    page,
    page_size: PAGE_SIZE,
  });
  const deleteMutation = useDeleteBranch();
  const rows = data?.items ?? [];
  const hasFilters = Boolean(q || branchType || status !== "active");

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

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div className="w-64 sm:w-72">
                <Label htmlFor="branch_search">Поиск</Label>
                <Input
                  id="branch_search"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Название, адрес или лицензия"
                />
              </div>
            ),
            active: Boolean(qInput.trim()),
            onClear: () => {
              setQInput("");
              setQ("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "type",
            label: "Тип точки",
            content: (
              <div>
                <Label htmlFor="branch_type_filter">Тип точки</Label>
                <Select
                  id="branch_type_filter"
                  value={branchType}
                  onChange={(event) => {
                    setBranchType(event.target.value as BranchType | "");
                    setPage(1);
                  }}
                  className="w-44"
                >
                  <option value="">Все типы</option>
                  {(Object.keys(branchTypeLabel) as BranchType[]).map((type) => (
                    <option key={type} value={type}>
                      {branchTypeLabel[type]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(branchType),
            onClear: () => {
              setBranchType("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "status",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="branch_status_filter">Статус</Label>
                <Select
                  id="branch_status_filter"
                  value={status}
                  onChange={(event) => {
                    setStatus(event.target.value as StatusFilter);
                    setPage(1);
                  }}
                  className="w-40"
                >
                  <option value="active">Активные</option>
                  <option value="inactive">Неактивные</option>
                  <option value="all">Все</option>
                </Select>
              </div>
            ),
            active: status !== "active",
            onClear: () => {
              setStatus("active");
              setPage(1);
            },
            defaultVisible: true,
          },
        ]}
        onResetValues={() => {
          setQInput("");
          setQ("");
          setBranchType("");
          setStatus("active");
          setPage(1);
        }}
      />

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить список")}
        </p>
      )}
      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : rows.length === 0 ? (
        hasFilters ? (
          <TableEmpty title="Ничего не найдено">Измените запрос или выбранные фильтры.</TableEmpty>
        ) : (
          <TableEmpty>Пока нет ни одной точки</TableEmpty>
        )
      ) : (
        <>
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
              {rows.map((branch) => (
                <TR key={branch.id}>
                  <TD className="font-medium">{branch.name}</TD>
                  <TD>{branchTypeLabel[branch.branch_type]}</TD>
                  <TD className="max-w-xs truncate">{branch.address ?? "—"}</TD>
                  <TD>
                    {branch.license_number ?? "—"}
                    {branch.license_expires_at && (
                      <span className="ml-2 text-xs text-foreground-muted">
                        до {new Date(branch.license_expires_at).toLocaleDateString("ru-RU")}
                      </span>
                    )}
                  </TD>
                  <TD>
                    {branch.is_active ? (
                      <Badge tone="success">Активна</Badge>
                    ) : (
                      <Badge tone="neutral">Неактивна</Badge>
                    )}
                  </TD>
                  {showActions && (
                    <TD className="text-right">
                      {canUpdate && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={isFetching}
                          onClick={() => setEditing(branch)}
                        >
                          Изменить
                        </Button>
                      )}
                      {canDelete && branch.is_active && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={isFetching}
                          onClick={() => {
                            setDeleteError(null);
                            setPendingDelete(branch);
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
          <Pagination page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} onPage={setPage} />
        </>
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
