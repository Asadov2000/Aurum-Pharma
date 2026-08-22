import { useEffect, useMemo, useState } from "react";

import {
  ActionMenu,
  Badge,
  Button,
  ConfigurableFilterBar,
  ConfirmDialog,
  Input,
  Label,
  Modal,
  PageHeader,
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
import { describeApiError } from "@/features/foundation/errors";

import { BarcodesPanel } from "./BarcodesPanel";
import { CatalogItemDetails } from "./CatalogItemDetails";
import { CatalogItemForm } from "./CatalogItemForm";
import { ImportWizard } from "./ImportWizard";
import { dispensingLabel, dispensingOptions, storageLabel, storageOptions } from "./labels";
import { useCatalogQuery, useDeleteCatalogItem, useRestoreCatalogItem } from "./queries";
import { type CatalogItem, type DispensingType, type StorageType } from "./types";

type LifecycleFilter = "active" | "inactive" | "archived" | "all";

const pageSizeOptions = [25, 50, 100] as const;

function itemStatus(item: CatalogItem): {
  label: string;
  tone: "neutral" | "success" | "warning";
} {
  if (item.deleted_at) return { label: "Архив", tone: "neutral" };
  if (!item.is_active) return { label: "Отключена", tone: "warning" };
  return { label: "В работе", tone: "success" };
}

function formattedPrice(item: CatalogItem): string {
  return item.base_price ? `${Number(item.base_price).toFixed(2)} ${item.currency}` : "—";
}

export function CatalogPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("catalog");
  const canCreate = hasPermission(user, "catalog.create");
  const canUpdate = hasPermission(user, "catalog.update");
  const canDelete = hasPermission(user, "catalog.delete");
  const canImport = canCreate && canUpdate;

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [manufacturerInput, setManufacturerInput] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [categoryInput, setCategoryInput] = useState("");
  const [category, setCategory] = useState("");
  const [dispensing, setDispensing] = useState<DispensingType | "">("");
  const [storage, setStorage] = useState<StorageType | "">("");
  const [lifecycle, setLifecycle] = useState<LifecycleFilter>("active");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<(typeof pageSizeOptions)[number]>(25);
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<CatalogItem | null>(null);
  const [editing, setEditing] = useState<CatalogItem | null>(null);
  const [importing, setImporting] = useState(false);
  const [confirmItem, setConfirmItem] = useState<CatalogItem | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim());
      setManufacturer(manufacturerInput.trim());
      setCategory(categoryInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [categoryInput, manufacturerInput, qInput]);

  const params = useMemo(
    () => ({
      q: q || undefined,
      manufacturer: manufacturer || undefined,
      category: category || undefined,
      dispensing_type: dispensing || undefined,
      storage_type: storage || undefined,
      lifecycle,
      page,
      page_size: pageSize,
    }),
    [category, dispensing, lifecycle, manufacturer, page, pageSize, q, storage],
  );
  const query = useCatalogQuery(params);
  const deleteMutation = useDeleteCatalogItem();
  const restoreMutation = useRestoreCatalogItem();
  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const hasFilters = Boolean(
    qInput.trim() ||
    manufacturerInput.trim() ||
    categoryInput.trim() ||
    dispensing ||
    storage ||
    lifecycle !== "active",
  );

  useEffect(() => {
    if (!query.data) return;
    const lastPage = Math.max(1, Math.ceil(query.data.total / pageSize));
    if (page > lastPage) setPage(lastPage);
  }, [page, pageSize, query.data]);

  const resetFilters = () => {
    setQInput("");
    setQ("");
    setManufacturerInput("");
    setManufacturer("");
    setCategoryInput("");
    setCategory("");
    setDispensing("");
    setStorage("");
    setLifecycle("active");
    setPage(1);
  };

  const doArchive = async () => {
    if (!confirmItem) return;
    setActionError(null);
    try {
      await deleteMutation.mutateAsync(confirmItem.id);
      setConfirmItem(null);
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось архивировать позицию"));
    }
  };

  const doRestore = async (item: CatalogItem) => {
    setActionError(null);
    try {
      await restoreMutation.mutateAsync(item.id);
    } catch (error) {
      setActionError(describeApiError(error, "Не удалось восстановить позицию"));
    }
  };

  const rowActions = (item: CatalogItem) => [
    { label: "Открыть", onSelect: () => setViewing(item) },
    ...(!item.deleted_at && canUpdate
      ? [{ label: "Изменить", onSelect: () => setEditing(item) }]
      : []),
    ...(!item.deleted_at && canDelete
      ? [
          {
            label: "Архивировать",
            onSelect: () => setConfirmItem(item),
            tone: "danger" as const,
          },
        ]
      : []),
    ...(item.deleted_at && canDelete
      ? [{ label: "Восстановить", onSelect: () => void doRestore(item) }]
      : []),
  ];

  const importStorageKey = `aurum:catalog-import:v1:${user?.active_tenant_id ?? "none"}:${user?.id ?? "unknown"}`;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Каталог"
        description="Лекарства, цены, условия отпуска и штрихкоды аптеки."
        meta={
          query.data ? (
            <span aria-live="polite">
              {query.data.total} найдено
              {query.isFetching && !query.isLoading ? " · обновление" : ""}
            </span>
          ) : undefined
        }
        actions={
          canCreate || canImport ? (
            <>
              {canImport && (
                <Button variant="secondary" onClick={() => setImporting(true)}>
                  Импорт из файла
                </Button>
              )}
              {canCreate && <Button onClick={() => setCreating(true)}>+ Новая позиция</Button>}
            </>
          ) : undefined
        }
      />

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div className="w-full sm:w-80">
                <Label htmlFor="catalog_search">Поиск</Label>
                <Input
                  id="catalog_search"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Название, МНН или штрихкод"
                  autoComplete="off"
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
            id: "lifecycle",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="catalog_lifecycle">Статус</Label>
                <Select
                  id="catalog_lifecycle"
                  value={lifecycle}
                  onChange={(event) => {
                    setLifecycle(event.target.value as LifecycleFilter);
                    setPage(1);
                  }}
                  className="w-full sm:w-44"
                >
                  <option value="active">В работе</option>
                  <option value="inactive">Отключённые</option>
                  <option value="archived">Архив</option>
                  <option value="all">Все статусы</option>
                </Select>
              </div>
            ),
            active: lifecycle !== "active",
            onClear: () => {
              setLifecycle("active");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "dispensing",
            label: "Тип отпуска",
            content: (
              <div>
                <Label htmlFor="catalog_dispensing">Тип отпуска</Label>
                <Select
                  id="catalog_dispensing"
                  value={dispensing}
                  onChange={(event) => {
                    setDispensing(event.target.value as DispensingType | "");
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                >
                  <option value="">Все</option>
                  {dispensingOptions.map((option) => (
                    <option key={option} value={option}>
                      {dispensingLabel[option]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(dispensing),
            onClear: () => {
              setDispensing("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "manufacturer",
            label: "Производитель",
            content: (
              <div>
                <Label htmlFor="catalog_manufacturer">Производитель</Label>
                <Input
                  id="catalog_manufacturer"
                  value={manufacturerInput}
                  onChange={(event) => setManufacturerInput(event.target.value)}
                  placeholder="Название компании"
                  autoComplete="off"
                  className="w-full sm:w-56"
                />
              </div>
            ),
            active: Boolean(manufacturerInput.trim()),
            onClear: () => {
              setManufacturerInput("");
              setManufacturer("");
              setPage(1);
            },
          },
          {
            id: "category",
            label: "Категория",
            content: (
              <div>
                <Label htmlFor="catalog_category">Категория</Label>
                <Input
                  id="catalog_category"
                  value={categoryInput}
                  onChange={(event) => setCategoryInput(event.target.value)}
                  placeholder="Например, обезболивающие"
                  autoComplete="off"
                  className="w-full sm:w-56"
                />
              </div>
            ),
            active: Boolean(categoryInput.trim()),
            onClear: () => {
              setCategoryInput("");
              setCategory("");
              setPage(1);
            },
          },
          {
            id: "storage",
            label: "Хранение",
            content: (
              <div>
                <Label htmlFor="catalog_storage">Хранение</Label>
                <Select
                  id="catalog_storage"
                  value={storage}
                  onChange={(event) => {
                    setStorage(event.target.value as StorageType | "");
                    setPage(1);
                  }}
                  className="w-full sm:w-48"
                >
                  <option value="">Все условия</option>
                  {storageOptions.map((option) => (
                    <option key={option} value={option}>
                      {storageLabel[option]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(storage),
            onClear: () => {
              setStorage("");
              setPage(1);
            },
          },
        ]}
        onResetValues={resetFilters}
      />

      {actionError && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2"
          role="alert"
        >
          <span className="text-sm text-danger-foreground">{actionError}</span>
          <Button variant="ghost" size="sm" onClick={() => setActionError(null)}>
            Закрыть
          </Button>
        </div>
      )}

      {query.error && !query.data ? (
        <TableEmpty
          title="Каталог не загрузился"
          action={<Button onClick={() => void query.refetch()}>Повторить</Button>}
        >
          {describeApiError(query.error, "Проверьте соединение и повторите попытку")}
        </TableEmpty>
      ) : query.isLoading ? (
        <SkeletonRows rows={6} />
      ) : rows.length === 0 ? (
        hasFilters ? (
          <TableEmpty
            title="Ничего не найдено"
            action={
              <Button variant="secondary" onClick={resetFilters}>
                Сбросить фильтры
              </Button>
            }
          >
            Измените запрос или верните стандартные фильтры.
          </TableEmpty>
        ) : (
          <TableEmpty
            title="Каталог пуст"
            action={
              canCreate || canImport ? (
                <div className="flex flex-wrap justify-center gap-2">
                  {canImport && (
                    <Button variant="secondary" onClick={() => setImporting(true)}>
                      Импорт из файла
                    </Button>
                  )}
                  {canCreate && <Button onClick={() => setCreating(true)}>+ Новая позиция</Button>}
                </div>
              ) : undefined
            }
          >
            {canCreate || canImport
              ? "Добавьте первую позицию вручную или импортируйте прайс из файла."
              : "В аптеке пока нет доступных позиций."}
          </TableEmpty>
        )
      ) : (
        <>
          {query.error && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2">
              <p className="text-sm text-foreground-secondary" role="status">
                Показаны ранее загруженные данные. Обновление не удалось.
              </p>
              <Button variant="secondary" size="sm" onClick={() => void query.refetch()}>
                Повторить
              </Button>
            </div>
          )}

          <div className="hidden md:block">
            <Table className="min-w-[860px] table-fixed">
              <THead>
                <TR>
                  <TH className="w-[24%]">Позиция</TH>
                  <TH className="w-[18%]">Производитель</TH>
                  <TH className="w-[22%]">Форма выпуска</TH>
                  <TH className="w-[14%]">Отпуск</TH>
                  <TH className="w-[11%]">Цена</TH>
                  <TH className="w-[9%]">Статус</TH>
                  <TH className="w-12 text-right">
                    <span className="sr-only">Действия</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((item) => {
                  const status = itemStatus(item);
                  return (
                    <TR key={item.id}>
                      <TD>
                        <p className="break-words font-medium">{item.brand_name}</p>
                        <p className="mt-0.5 break-words text-xs text-foreground-muted">
                          {item.inn || "МНН не указано"}
                        </p>
                      </TD>
                      <TD className="break-words">{item.manufacturer ?? "—"}</TD>
                      <TD className="break-words">
                        {[item.form, item.dosage].filter(Boolean).join(" · ") || "—"}
                        {item.pack_size && (
                          <span className="mt-0.5 block text-xs text-foreground-muted">
                            {item.pack_size}
                          </span>
                        )}
                      </TD>
                      <TD className="break-words">{dispensingLabel[item.dispensing_type]}</TD>
                      <TD className="whitespace-nowrap">{formattedPrice(item)}</TD>
                      <TD className="whitespace-nowrap">
                        <Badge tone={status.tone}>{status.label}</Badge>
                      </TD>
                      <TD className="w-12 text-right">
                        <ActionMenu
                          label={`Действия для ${item.brand_name}`}
                          items={rowActions(item)}
                        />
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          </div>

          <div className="grid grid-cols-1 gap-2 md:hidden">
            {rows.map((item) => {
              const status = itemStatus(item);
              return (
                <article key={item.id} className="rounded-lg border border-border bg-surface p-3">
                  <div className="flex min-w-0 items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="break-words text-sm font-semibold text-foreground">
                        {item.brand_name}
                      </h2>
                      <p className="mt-0.5 break-words text-xs text-foreground-muted">
                        {item.inn || item.manufacturer || "Дополнительные сведения не указаны"}
                      </p>
                    </div>
                    <ActionMenu
                      label={`Действия для ${item.brand_name}`}
                      items={rowActions(item)}
                    />
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
                    <Badge tone={status.tone}>{status.label}</Badge>
                    <span className="text-foreground-secondary">
                      {[item.form, item.dosage].filter(Boolean).join(" · ") || "Форма не указана"}
                    </span>
                    <span className="ml-auto font-semibold text-foreground">
                      {formattedPrice(item)}
                    </span>
                  </div>
                </article>
              );
            })}
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="flex items-center gap-2 text-sm">
              <Label htmlFor="catalog_page_size" className="mb-0 whitespace-nowrap">
                На странице
              </Label>
              <Select
                id="catalog_page_size"
                value={pageSize}
                onChange={(event) => {
                  setPageSize(Number(event.target.value) as (typeof pageSizeOptions)[number]);
                  setPage(1);
                }}
                className="w-20"
              >
                {pageSizeOptions.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </Select>
            </div>
            <div className="min-w-0 flex-1">
              <Pagination page={page} pageSize={pageSize} total={total} onPage={setPage} />
            </div>
          </div>
        </>
      )}

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="Новая позиция"
        className="max-w-2xl"
      >
        <CatalogItemForm item={null} onClose={() => setCreating(false)} />
      </Modal>

      <Modal
        open={viewing !== null}
        onClose={() => setViewing(null)}
        title="Карточка позиции"
        className="max-w-2xl"
      >
        {viewing && (
          <div className="space-y-4">
            <CatalogItemDetails item={viewing} />
            <BarcodesPanel itemId={viewing.id} canManage={false} />
            <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
              <Button variant="secondary" onClick={() => setViewing(null)}>
                Закрыть
              </Button>
              {canUpdate && !viewing.deleted_at && (
                <Button
                  onClick={() => {
                    setViewing(null);
                    setEditing(viewing);
                  }}
                >
                  Изменить
                </Button>
              )}
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing ? `Изменить: ${editing.brand_name}` : ""}
        className="max-w-2xl"
      >
        {editing && (
          <div className="space-y-4">
            <CatalogItemForm item={editing} onClose={() => setEditing(null)} />
            <BarcodesPanel itemId={editing.id} canManage={canUpdate} />
          </div>
        )}
      </Modal>

      <Modal
        open={importing}
        onClose={() => setImporting(false)}
        title="Импорт каталога"
        className="max-w-3xl"
      >
        <ImportWizard
          onClose={() => setImporting(false)}
          canRollback={canDelete}
          storageKey={importStorageKey}
        />
      </Modal>

      <ConfirmDialog
        open={confirmItem !== null}
        title="Архивировать позицию"
        message={
          <>
            Архивировать «{confirmItem?.brand_name}»? Она исчезнет из рабочего каталога, но её можно
            будет восстановить через фильтр «Архив».
            {actionError && <span className="mt-2 block text-danger">{actionError}</span>}
          </>
        }
        confirmLabel="Архивировать"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => void doArchive()}
        onCancel={() => {
          setConfirmItem(null);
          setActionError(null);
        }}
      />
    </div>
  );
}
