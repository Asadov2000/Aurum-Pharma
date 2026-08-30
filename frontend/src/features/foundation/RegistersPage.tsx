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

import { describeApiError, describeFoundationError } from "./errors";
import { LocationsSummary, LocationsWorkspaceHeader } from "./LocationsWorkspace";
import { useBranchesQuery, useDeleteRegister, useRegisterSearchQuery } from "./queries";
import { RegisterForm } from "./RegisterForm";
import { type PrinterType, type Register } from "./types";

const PAGE_SIZE = 25;

const printerLabel: Record<PrinterType, string> = {
  browser: "Браузер",
  thermal_58: "58 мм",
  thermal_80: "80 мм",
  a4: "A4",
};

type StatusFilter = "active" | "inactive" | "all";

export function RegistersPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("registers");
  const canCreate = hasPermission(user, "registers.create");
  const canUpdate = hasPermission(user, "registers.update");
  const canDelete = hasPermission(user, "registers.delete");
  const canViewBranches = hasPermission(user, "branches.view");
  const showActions = canUpdate || canDelete;
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [branchFilter, setBranchFilter] = useState("");
  const [printerType, setPrinterType] = useState<PrinterType | "">("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Register | null>(null);
  const [creating, setCreating] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Register | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [qInput]);

  // registers.view is itself a branch-discovery capability on the backend.
  // Loading names here avoids exposing internal branch IDs to restricted roles.
  const branches = useBranchesQuery(true);
  const { data, isLoading, isFetching, error, refetch } = useRegisterSearchQuery({
    q,
    branch_id: branchFilter || undefined,
    printer_type: printerType || undefined,
    is_active: status === "all" ? undefined : status === "active",
    page,
    page_size: PAGE_SIZE,
  });
  const deleteMutation = useDeleteRegister();
  const rows = data?.items ?? [];
  const hasInitialLoadError = Boolean(error && data === undefined);
  const hasFilters = Boolean(q || branchFilter || printerType || status !== "active");
  const activeOnPage = rows.filter((register) => register.is_active).length;
  const printersOnPage = rows.filter((register) => register.printer_type !== null).length;

  const branchNameById = (id: string): string =>
    branches.data?.find((branch) => branch.id === id)?.name ?? `Точка ${id.slice(0, 8)}`;

  const closeEditor = () => {
    setCreating(false);
    setEditing(null);
    setEditorDirty(false);
    setDiscardOpen(false);
  };

  const requestEditorClose = () => {
    if (editorDirty) setDiscardOpen(true);
    else closeEditor();
  };

  const confirmDelete = async () => {
    if (!pendingDelete || !canDelete) return;
    setDeleteError(null);
    try {
      await deleteMutation.mutateAsync(pendingDelete.id);
      setPendingDelete(null);
    } catch (err) {
      setDeleteError(describeFoundationError(err, "Не удалось деактивировать рабочую кассу"));
    }
  };

  return (
    <div className="space-y-4">
      <LocationsWorkspaceHeader
        active="registers"
        showBranches={canViewBranches}
        showRegisters
        meta={isFetching && !isLoading ? "Обновление…" : undefined}
        actions={
          canCreate ? (
            <Button onClick={() => setCreating(true)}>Добавить рабочую кассу</Button>
          ) : undefined
        }
      />

      {!hasInitialLoadError && (
        <LocationsSummary
          label="Сводка касс"
          loading={isLoading}
          metrics={[
            { label: "Всего по фильтрам", value: data?.total ?? 0 },
            { label: "Показано на странице", value: rows.length },
            { label: "Активных на странице", value: activeOnPage, tone: "success" },
            { label: "Формат чека выбран на странице", value: printersOnPage },
          ]}
        />
      )}

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div className="w-64 sm:w-72">
                <Label htmlFor="register_search">Поиск</Label>
                <Input
                  id="register_search"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Название кассы"
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
            id: "branch",
            label: "Торговая точка",
            content: (
              <div>
                <Label htmlFor="register_branch_filter">Торговая точка</Label>
                <Select
                  id="register_branch_filter"
                  value={branchFilter}
                  onChange={(event) => {
                    setBranchFilter(event.target.value);
                    setPage(1);
                  }}
                  className="w-52"
                >
                  <option value="">Все торговые точки</option>
                  {branches.data?.map((branch) => (
                    <option key={branch.id} value={branch.id}>
                      {branch.name}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(branchFilter),
            onClear: () => {
              setBranchFilter("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "printer",
            label: "Формат чека",
            content: (
              <div>
                <Label htmlFor="register_printer_filter">Формат чека</Label>
                <Select
                  id="register_printer_filter"
                  value={printerType}
                  onChange={(event) => {
                    setPrinterType(event.target.value as PrinterType | "");
                    setPage(1);
                  }}
                  className="w-40"
                >
                  <option value="">Все типы</option>
                  {(Object.keys(printerLabel) as PrinterType[]).map((type) => (
                    <option key={type} value={type}>
                      {printerLabel[type]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(printerType),
            onClear: () => {
              setPrinterType("");
              setPage(1);
            },
          },
          {
            id: "status",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="register_status_filter">Статус</Label>
                <Select
                  id="register_status_filter"
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
          setBranchFilter("");
          setPrinterType("");
          setStatus("active");
          setPage(1);
        }}
      />

      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger-subtle px-4 py-3 text-sm leading-5 text-danger-foreground"
        >
          <span>{describeApiError(error, "Не удалось загрузить рабочие кассы")}</span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              void refetch();
            }}
          >
            Повторить
          </Button>
        </div>
      )}
      {branches.error && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warning/30 bg-warning-subtle px-4 py-3 text-sm leading-5 text-warning-foreground"
        >
          <span>
            Названия торговых точек не загрузились. Рабочие кассы доступны, но временно показаны с
            короткими кодами.
          </span>
          <Button variant="secondary" size="sm" onClick={() => void branches.refetch()}>
            Повторить
          </Button>
        </div>
      )}
      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : hasInitialLoadError ? null : rows.length === 0 ? (
        hasFilters ? (
          <TableEmpty title="Ничего не найдено">Измените запрос или выбранные фильтры.</TableEmpty>
        ) : (
          <TableEmpty
            title="Рабочих касс пока нет"
            action={
              canCreate ? (
                <Button onClick={() => setCreating(true)}>Добавить рабочую кассу</Button>
              ) : undefined
            }
          >
            Добавьте рабочее место продаж и привяжите его к торговой точке.
          </TableEmpty>
        )
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Название</TH>
                <TH>Торговая точка</TH>
                <TH>Формат чека</TH>
                <TH>Статус</TH>
                {showActions && <TH className="text-right">Действия</TH>}
              </TR>
            </THead>
            <TBody>
              {rows.map((register) => (
                <TR key={register.id}>
                  <TD>
                    <span className="block font-semibold">{register.name}</span>
                    <span className="mt-0.5 block text-xs text-foreground-muted">
                      Создана {new Date(register.created_at).toLocaleDateString("ru-RU")}
                    </span>
                  </TD>
                  <TD className="font-medium">{branchNameById(register.branch_id)}</TD>
                  <TD>
                    {register.printer_type ? (
                      printerLabel[register.printer_type]
                    ) : (
                      <span className="text-foreground-muted">Не настроен</span>
                    )}
                  </TD>
                  <TD>
                    {register.is_active ? (
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
                          onClick={() => setEditing(register)}
                        >
                          Открыть
                        </Button>
                      )}
                      {canDelete && register.is_active && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={isFetching}
                          onClick={() => {
                            setDeleteError(null);
                            setPendingDelete(register);
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
          onClose={requestEditorClose}
          title={editing ? `Изменить рабочую кассу: ${editing.name}` : "Добавить рабочую кассу"}
        >
          <RegisterForm
            register={editing}
            branchName={editing ? branchNameById(editing.branch_id) : null}
            onClose={closeEditor}
            onCancel={requestEditorClose}
            onDirtyChange={setEditorDirty}
          />
        </Modal>
      )}
      {canDelete && (
        <ConfirmDialog
          open={pendingDelete !== null}
          title="Деактивировать рабочую кассу"
          message={
            <>
              Деактивировать рабочую кассу «{pendingDelete?.name}»? На ней нельзя будет открыть
              новую смену или провести продажу.
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
      <ConfirmDialog
        open={discardOpen}
        title="Закрыть без сохранения?"
        message="Изменения рабочей кассы не сохранятся."
        cancelLabel="Продолжить редактирование"
        confirmLabel="Закрыть без сохранения"
        variant="danger"
        onCancel={() => setDiscardOpen(false)}
        onConfirm={closeEditor}
      />
    </div>
  );
}
