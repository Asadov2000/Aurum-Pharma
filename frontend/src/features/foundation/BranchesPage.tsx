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
import { LocationsSummary, LocationsWorkspaceHeader } from "./LocationsWorkspace";
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

  const { data, isLoading, isFetching, error, refetch } = useBranchSearchQuery({
    q,
    branch_type: branchType || undefined,
    is_active: status === "all" ? undefined : status === "active",
    page,
    page_size: PAGE_SIZE,
  });
  const deleteMutation = useDeleteBranch();
  const rows = data?.items ?? [];
  const hasFilters = Boolean(q || branchType || status !== "active");
  const activeOnPage = rows.filter((branch) => branch.is_active).length;
  const licenseAttentionOnPage = rows.filter((branch) =>
    licenseNeedsAttention(branch.license_number, branch.license_expires_at),
  ).length;

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
      <LocationsWorkspaceHeader
        active="branches"
        meta={isFetching && !isLoading ? "Обновление…" : undefined}
        actions={
          canCreate ? <Button onClick={() => setCreating(true)}>+ Новая точка</Button> : undefined
        }
      />

      <LocationsSummary
        label="Сводка торговых точек"
        loading={isLoading}
        metrics={[
          { label: "Найдено", value: data?.total ?? 0 },
          { label: "На странице", value: rows.length },
          { label: "Активны на странице", value: activeOnPage, tone: "success" },
          {
            label: "Лицензии требуют внимания",
            value: licenseAttentionOnPage,
            tone: licenseAttentionOnPage > 0 ? "warning" : "default",
          },
        ]}
      />

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
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm leading-5 text-danger-foreground"
        >
          <span>{describeApiError(error, "Не удалось загрузить список точек")}</span>
          <Button variant="secondary" size="sm" onClick={() => void refetch()}>
            Повторить
          </Button>
        </div>
      )}
      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : rows.length === 0 ? (
        hasFilters ? (
          <TableEmpty title="Ничего не найдено">Измените запрос или выбранные фильтры.</TableEmpty>
        ) : (
          <TableEmpty
            title="Торговых точек пока нет"
            action={
              canCreate ? (
                <Button onClick={() => setCreating(true)}>+ Новая точка</Button>
              ) : undefined
            }
          >
            Добавьте первую аптеку или аптечный пункт, чтобы подключить кассы и вести остатки.
          </TableEmpty>
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
                <TH>Реквизиты чека</TH>
                <TH>Статус</TH>
                {showActions && <TH className="text-right">Действия</TH>}
              </TR>
            </THead>
            <TBody>
              {rows.map((branch) => (
                <TR key={branch.id}>
                  <TD>
                    <span className="block font-semibold">{branch.name}</span>
                    <span className="mt-0.5 block text-xs text-foreground-muted">
                      Создана {new Date(branch.created_at).toLocaleDateString("ru-RU")}
                    </span>
                  </TD>
                  <TD>
                    <Badge tone="neutral">{branchTypeLabel[branch.branch_type]}</Badge>
                  </TD>
                  <TD className="max-w-sm whitespace-normal leading-5">
                    {branch.address ?? <span className="text-foreground-muted">Не указан</span>}
                  </TD>
                  <TD>{renderLicense(branch.license_number, branch.license_expires_at)}</TD>
                  <TD>
                    {branch.receipt_header?.line1 ? (
                      <Badge tone="success">Заполнены</Badge>
                    ) : (
                      <Badge tone="warning">Нужно заполнить</Badge>
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
                          Открыть
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
                          Деактивировать
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
              {deleteError && (
                <span
                  role="alert"
                  className="mt-2 block rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-danger-foreground"
                >
                  {deleteError}
                </span>
              )}
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

function licenseNeedsAttention(number: string | null, expiresAt: string | null): boolean {
  if (!number || !expiresAt) return true;
  const expires = new Date(`${expiresAt}T23:59:59`);
  if (Number.isNaN(expires.getTime())) return true;
  const attentionLimit = new Date();
  attentionLimit.setDate(attentionLimit.getDate() + 60);
  return expires <= attentionLimit;
}

function renderLicense(number: string | null, expiresAt: string | null): JSX.Element {
  if (!number) {
    return <Badge tone="warning">Не указана</Badge>;
  }
  if (!expiresAt) {
    return (
      <div>
        <span className="block font-medium">{number}</span>
        <span className="text-xs text-foreground-muted">Срок не указан</span>
      </div>
    );
  }

  const expires = new Date(`${expiresAt}T23:59:59`);
  const now = new Date();
  const attentionLimit = new Date();
  attentionLimit.setDate(attentionLimit.getDate() + 60);
  const tone = expires < now ? "danger" : expires <= attentionLimit ? "warning" : "success";
  const status = expires < now ? "Истекла" : expires <= attentionLimit ? "Скоро истекает" : null;

  return (
    <div className="space-y-1">
      <span className="block font-medium">{number}</span>
      <span className="block text-xs text-foreground-muted">
        до {expires.toLocaleDateString("ru-RU")}
      </span>
      {status && <Badge tone={tone}>{status}</Badge>}
    </div>
  );
}
