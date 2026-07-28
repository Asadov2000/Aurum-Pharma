import { useState } from "react";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
  Label,
  Modal,
  Pagination,
  Select,
  SkeletonRows,
  Switch,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { CatalogPicker } from "@/features/catalog/CatalogPicker";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";

import { BatchDetailModal } from "./BatchDetailModal";
import { expiryLabel, expiryOptions, expiryTone } from "./labels";
import { useBatchesQuery } from "./queries";
import { type ExpiryStatus } from "./types";

const PAGE_SIZE = 50;

export function BatchesPage(): JSX.Element {
  const filterPreferenceKey = useFilterPreferenceKey("batches");
  const [branchId, setBranchId] = useState("");
  const [catalogId, setCatalogId] = useState("");
  const [expiry, setExpiry] = useState<ExpiryStatus | "">("");
  const [showEmpty, setShowEmpty] = useState(false);
  const [page, setPage] = useState(1);
  const [openBatchId, setOpenBatchId] = useState<string | null>(null);

  const branches = useBranchesQuery(true);
  const { data, isLoading, error } = useBatchesQuery({
    branch_id: branchId || undefined,
    catalog_id: catalogId || undefined,
    expiry_status: expiry || undefined,
    show_empty: showEmpty,
    page,
    page_size: PAGE_SIZE,
  });

  const total = data?.total ?? 0;

  const branchNameById = (id: string): string =>
    branches.data?.find((b) => b.id === id)?.name ?? id.slice(0, 8);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Партии</h1>
        <span className="text-sm text-foreground-muted">
          Создание партий — через приёмку поставщиков
        </span>
      </div>

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "product",
            label: "Товар",
            content: (
              <div className="w-64 sm:w-72">
                <Label htmlFor="catalog">Товар</Label>
                <CatalogPicker
                  value={catalogId}
                  onChange={(id) => {
                    setCatalogId(id);
                    setPage(1);
                  }}
                  placeholder="Найти по названию…"
                  clearable
                />
              </div>
            ),
            active: Boolean(catalogId),
            onClear: () => {
              setCatalogId("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "branch",
            label: "Точка",
            content: (
              <div>
                <Label htmlFor="branch">Точка</Label>
                <Select
                  id="branch"
                  value={branchId}
                  onChange={(e) => {
                    setBranchId(e.target.value);
                    setPage(1);
                  }}
                  className="w-56"
                >
                  <option value="">Все точки</option>
                  {branches.data?.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(branchId),
            onClear: () => {
              setBranchId("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "expiry",
            label: "Срок годности",
            content: (
              <div>
                <Label htmlFor="expiry">Срок годности</Label>
                <Select
                  id="expiry"
                  value={expiry}
                  onChange={(e) => {
                    setExpiry(e.target.value as ExpiryStatus | "");
                    setPage(1);
                  }}
                  className="w-56"
                >
                  <option value="">Все</option>
                  {expiryOptions.map((s) => (
                    <option key={s} value={s}>
                      {expiryLabel[s]}
                    </option>
                  ))}
                </Select>
              </div>
            ),
            active: Boolean(expiry),
            onClear: () => {
              setExpiry("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "empty",
            label: "Пустые партии",
            content: (
              <div className="flex h-10 items-center">
                <Switch
                  label="Показывать пустые партии"
                  checked={showEmpty}
                  onChange={(e) => {
                    setShowEmpty(e.target.checked);
                    setPage(1);
                  }}
                />
              </div>
            ),
            active: showEmpty,
            onClear: () => {
              setShowEmpty(false);
              setPage(1);
            },
          },
        ]}
        onResetValues={() => {
          setBranchId("");
          setCatalogId("");
          setExpiry("");
          setShowEmpty(false);
          setPage(1);
        }}
      />

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить партии")}
        </p>
      )}

      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : !data || data.items.length === 0 ? (
        <TableEmpty>
          {branchId || catalogId || expiry || showEmpty
            ? "По текущим фильтрам ничего не найдено"
            : "Партии появятся после приёмки от поставщика"}
        </TableEmpty>
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Партия</TH>
                <TH>Точка</TH>
                <TH>Срок</TH>
                <TH>Остаток</TH>
                <TH>Цена продажи</TH>
                <TH>Статус</TH>
                <TH className="text-right">Действия</TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((b) => (
                <TR key={b.id}>
                  <TD className="font-mono text-xs" title={`id: ${b.id}`}>
                    {b.batch_number ?? "—"}
                  </TD>
                  <TD>{branchNameById(b.branch_id)}</TD>
                  <TD>
                    <div>{new Date(b.expires_at).toLocaleDateString("ru-RU")}</div>
                    <div className="text-xs text-foreground-muted">
                      {b.days_to_expiry >= 0
                        ? `через ${b.days_to_expiry} дн.`
                        : `${-b.days_to_expiry} дн. назад`}
                    </div>
                  </TD>
                  <TD className="font-mono">
                    {b.qty_remaining}
                    <span className="ml-1 text-xs text-foreground-muted">/ {b.qty_initial}</span>
                  </TD>
                  <TD>
                    {Number(b.sale_price).toFixed(2)} {b.currency}
                  </TD>
                  <TD>
                    <div className="flex flex-col gap-1">
                      <Badge tone={expiryTone[b.expiry_status]}>
                        {expiryLabel[b.expiry_status]}
                      </Badge>
                      {b.is_blocked && <Badge tone="danger">блок</Badge>}
                    </div>
                  </TD>
                  <TD className="text-right">
                    <Button variant="ghost" size="sm" onClick={() => setOpenBatchId(b.id)}>
                      Подробнее
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
        </>
      )}

      <Modal
        open={openBatchId !== null}
        onClose={() => setOpenBatchId(null)}
        title="Партия"
        className="max-w-3xl"
      >
        {openBatchId && (
          <BatchDetailModal batchId={openBatchId} onClose={() => setOpenBatchId(null)} />
        )}
      </Modal>
    </div>
  );
}
