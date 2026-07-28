import { useEffect, useState } from "react";

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
import { describeApiError } from "@/features/foundation/errors";

import { BarcodesPanel } from "./BarcodesPanel";
import { CatalogItemForm } from "./CatalogItemForm";
import { ImportWizard } from "./ImportWizard";
import { dispensingLabel, dispensingOptions } from "./labels";
import { useCatalogQuery, useDeleteCatalogItem } from "./queries";
import { type CatalogItem, type DispensingType } from "./types";

const PAGE_SIZE = 25;

export function CatalogPage(): JSX.Element {
  const filterPreferenceKey = useFilterPreferenceKey("catalog");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [dispensing, setDispensing] = useState<DispensingType | "">("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CatalogItem | null>(null);
  const [importing, setImporting] = useState(false);
  const [confirmItem, setConfirmItem] = useState<CatalogItem | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  // 300ms debounce so we don't fire on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setQ(qInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const { data, isLoading, error } = useCatalogQuery({
    q,
    dispensing_type: dispensing || undefined,
    page,
    page_size: PAGE_SIZE,
  });
  const deleteMutation = useDeleteCatalogItem();

  const total = data?.total ?? 0;

  const doArchive = async () => {
    if (!confirmItem) return;
    setConfirmError(null);
    try {
      await deleteMutation.mutateAsync(confirmItem.id);
      setConfirmItem(null);
    } catch (err) {
      setConfirmError(describeApiError(err, "Не удалось архивировать"));
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Каталог"
        actions={
          <>
            <Button variant="secondary" onClick={() => setImporting(true)}>
              Импорт из файла
            </Button>
            <Button onClick={() => setCreating(true)}>+ Новая позиция</Button>
          </>
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
                <Label htmlFor="q">Поиск (название, МНН, производитель)</Label>
                <Input
                  id="q"
                  value={qInput}
                  onChange={(e) => setQInput(e.target.value)}
                  placeholder="например: парацетамол"
                />
              </div>
            ),
            active: Boolean(qInput),
            onClear: () => {
              setQInput("");
              setQ("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "dispensing",
            label: "Тип отпуска",
            content: (
              <div>
                <Label htmlFor="dispensing">Тип отпуска</Label>
                <Select
                  id="dispensing"
                  value={dispensing}
                  onChange={(e) => {
                    setDispensing(e.target.value as DispensingType | "");
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                >
                  <option value="">Все</option>
                  {dispensingOptions.map((d) => (
                    <option key={d} value={d}>
                      {dispensingLabel[d]}
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
        ]}
        onResetValues={() => {
          setQInput("");
          setQ("");
          setDispensing("");
          setPage(1);
        }}
      />

      {error && (
        <p
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          {describeApiError(error, "Не удалось загрузить каталог")}
        </p>
      )}

      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : !data || data.items.length === 0 ? (
        q || dispensing ? (
          <TableEmpty title="Ничего не найдено">Измените запрос или фильтр отпуска.</TableEmpty>
        ) : (
          <TableEmpty
            title="Каталог пуст"
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Button variant="secondary" onClick={() => setImporting(true)}>
                  Импорт из файла
                </Button>
                <Button onClick={() => setCreating(true)}>+ Новая позиция</Button>
              </div>
            }
          >
            Добавьте первую позицию вручную или импортируйте прайс из файла.
          </TableEmpty>
        )
      ) : (
        <>
          <Table className="min-w-[720px] table-fixed">
            <THead>
              <TR>
                <TH className="w-[16%]">Торговое название</TH>
                <TH className="w-[23%]">МНН</TH>
                <TH className="w-[27%]">Форма / дозировка</TH>
                <TH className="w-[13%]">Отпуск</TH>
                <TH className="w-[10%]">Цена</TH>
                <TH className="w-[8%]">Статус</TH>
                <TH className="w-12 text-right">
                  <span className="sr-only">Действия</span>
                </TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((it) => (
                <TR key={it.id}>
                  <TD className="break-words font-medium">{it.brand_name}</TD>
                  <TD className="break-words">{it.inn ?? "—"}</TD>
                  <TD>
                    {[it.form, it.dosage].filter(Boolean).join(" / ") || "—"}
                    {it.pack_size && (
                      <span className="ml-2 text-xs text-foreground-muted">· {it.pack_size}</span>
                    )}
                  </TD>
                  <TD className="break-words">{dispensingLabel[it.dispensing_type]}</TD>
                  <TD className="whitespace-nowrap">
                    {it.base_price ? `${parseFloat(it.base_price).toFixed(2)} ${it.currency}` : "—"}
                  </TD>
                  <TD className="whitespace-nowrap">
                    {it.is_active ? (
                      <Badge tone="success">активна</Badge>
                    ) : (
                      <Badge tone="neutral">архив</Badge>
                    )}
                  </TD>
                  <TD className="w-12 text-right">
                    <ActionMenu
                      label={`Действия для ${it.brand_name}`}
                      items={[
                        { label: "Изменить", onSelect: () => setEditing(it) },
                        ...(it.is_active
                          ? [
                              {
                                label: "Архивировать",
                                onSelect: () => setConfirmItem(it),
                                tone: "danger" as const,
                              },
                            ]
                          : []),
                      ]}
                    />
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
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
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing ? `Позиция: ${editing.brand_name}` : ""}
        className="max-w-2xl"
      >
        {editing && (
          <div className="space-y-4">
            <CatalogItemForm item={editing} onClose={() => setEditing(null)} />
            <BarcodesPanel itemId={editing.id} />
          </div>
        )}
      </Modal>

      <Modal
        open={importing}
        onClose={() => setImporting(false)}
        title="Импорт каталога"
        className="max-w-2xl"
      >
        <ImportWizard onClose={() => setImporting(false)} />
      </Modal>

      <ConfirmDialog
        open={confirmItem !== null}
        title="Архивировать позицию"
        message={
          <>
            Архивировать «{confirmItem?.brand_name}»? Позицию можно будет вернуть позже.
            {confirmError && <span className="mt-2 block text-danger">{confirmError}</span>}
          </>
        }
        confirmLabel="Архивировать"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => void doArchive()}
        onCancel={() => {
          setConfirmItem(null);
          setConfirmError(null);
        }}
      />
    </div>
  );
}
