import { useEffect, useState } from "react";

import {
  Badge,
  Button,
  Input,
  Label,
  Modal,
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
import { describeApiError } from "@/features/foundation/errors";

import { BarcodesPanel } from "./BarcodesPanel";
import { CatalogItemForm } from "./CatalogItemForm";
import { ImportWizard } from "./ImportWizard";
import { dispensingLabel, dispensingOptions } from "./labels";
import { useCatalogQuery, useDeleteCatalogItem } from "./queries";
import { type CatalogItem, type DispensingType } from "./types";

const PAGE_SIZE = 25;

export function CatalogPage(): JSX.Element {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [dispensing, setDispensing] = useState<DispensingType | "">("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<CatalogItem | null>(null);
  const [importing, setImporting] = useState(false);

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
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const onDelete = async (item: CatalogItem) => {
    if (!window.confirm(`Архивировать позицию «${item.brand_name}»?`)) return;
    try {
      await deleteMutation.mutateAsync(item.id);
    } catch (err) {
      window.alert(describeApiError(err, "Не удалось архивировать"));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Каталог</h1>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setImporting(true)}>
            Импорт из файла
          </Button>
          <Button onClick={() => setCreating(true)}>+ Новая позиция</Button>
        </div>
      </div>

      <div className="flex items-end gap-4">
        <div className="flex-1">
          <Label htmlFor="q">Поиск (название, МНН, производитель)</Label>
          <Input
            id="q"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="например: парацетамол"
          />
        </div>
        <div>
          <Label htmlFor="dispensing">Тип отпуска</Label>
          <Select
            id="dispensing"
            value={dispensing}
            onChange={(e) => {
              setDispensing(e.target.value as DispensingType | "");
              setPage(1);
            }}
            className="w-52"
          >
            <option value="">Все</option>
            {dispensingOptions.map((d) => (
              <option key={d} value={d}>
                {dispensingLabel[d]}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить каталог")}
        </p>
      )}

      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : !data || data.items.length === 0 ? (
        q || dispensing ? (
          <TableEmpty title="Ничего не найдено">
            Измените запрос или фильтр отпуска.
          </TableEmpty>
        ) : (
          <TableEmpty
            icon="💊"
            title="Каталог пуст"
            action={
              <div className="flex justify-center gap-2">
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
          <Table>
            <THead>
              <TR>
                <TH>Торговое название</TH>
                <TH>МНН</TH>
                <TH>Форма / дозировка</TH>
                <TH>Отпуск</TH>
                <TH>Цена</TH>
                <TH>Статус</TH>
                <TH className="text-right">Действия</TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((it) => (
                <TR key={it.id}>
                  <TD className="font-medium">{it.brand_name}</TD>
                  <TD>{it.inn ?? "—"}</TD>
                  <TD>
                    {[it.form, it.dosage].filter(Boolean).join(" / ") || "—"}
                    {it.pack_size && (
                      <span className="ml-2 text-xs text-foreground-muted">№ {it.pack_size}</span>
                    )}
                  </TD>
                  <TD>{dispensingLabel[it.dispensing_type]}</TD>
                  <TD>
                    {it.base_price
                      ? `${parseFloat(it.base_price).toFixed(2)} ${it.currency}`
                      : "—"}
                  </TD>
                  <TD>
                    {it.is_active ? (
                      <Badge tone="success">активна</Badge>
                    ) : (
                      <Badge tone="neutral">архив</Badge>
                    )}
                  </TD>
                  <TD className="text-right whitespace-nowrap">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(it)}>
                      Изменить
                    </Button>
                    {it.is_active && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void onDelete(it)}
                        isLoading={deleteMutation.isPending}
                      >
                        Архив
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <div className="flex items-center justify-between text-sm text-foreground-secondary">
            <span>
              Всего: <span className="font-medium">{total}</span>
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                ← Назад
              </Button>
              <span>
                Стр. {page} / {totalPages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Вперёд →
              </Button>
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
    </div>
  );
}
