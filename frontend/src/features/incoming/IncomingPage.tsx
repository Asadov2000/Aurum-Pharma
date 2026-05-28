import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";

import {
  Badge,
  Button,
  Label,
  Modal,
  Select,
  Table,
  TableEmpty,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useBranchesQuery } from "@/features/foundation/queries";
import { describeApiError } from "@/features/foundation/errors";
import { useSuppliersQuery } from "@/features/suppliers/queries";

import { NewIncomingForm } from "./NewIncomingForm";
import { useIncomingListQuery } from "./queries";
import { statusLabel, statusOptions, statusTone } from "./labels";
import { type IncomingStatus } from "./types";

export function IncomingPage(): JSX.Element {
  const [branchFilter, setBranchFilter] = useState<string>("");
  const [supplierFilter, setSupplierFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [creating, setCreating] = useState(false);

  const branches = useBranchesQuery(true);
  const suppliers = useSuppliersQuery(true);
  const params = useMemo(
    () => ({
      branch_id: branchFilter || undefined,
      supplier_id: supplierFilter || undefined,
      status: (statusFilter as IncomingStatus | "") || undefined,
    }),
    [branchFilter, supplierFilter, statusFilter],
  );
  const { data, isLoading, error } = useIncomingListQuery(params);

  const branchName = (id: string) =>
    branches.data?.find((b) => b.id === id)?.name ?? id.slice(0, 8);
  const supplierName = (id: string) =>
    suppliers.data?.find((s) => s.id === id)?.name ?? id.slice(0, 8);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Приходы</h1>
        <Button onClick={() => setCreating(true)}>+ Новый приход</Button>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <div>
          <Label htmlFor="branch_filter">Точка</Label>
          <Select
            id="branch_filter"
            value={branchFilter}
            onChange={(e) => setBranchFilter(e.target.value)}
            className="w-56"
          >
            <option value="">Все</option>
            {branches.data?.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="supplier_filter">Поставщик</Label>
          <Select
            id="supplier_filter"
            value={supplierFilter}
            onChange={(e) => setSupplierFilter(e.target.value)}
            className="w-56"
          >
            <option value="">Все</option>
            {suppliers.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="status_filter">Статус</Label>
          <Select
            id="status_filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="w-44"
          >
            <option value="">Все</option>
            {statusOptions.map((s) => (
              <option key={s} value={s}>
                {statusLabel[s]}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить приходы")}
        </p>
      )}
      {isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : !data || data.length === 0 ? (
        <TableEmpty>Приходов пока нет</TableEmpty>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Дата</TH>
              <TH>Номер</TH>
              <TH>Точка</TH>
              <TH>Поставщик</TH>
              <TH>Статус</TH>
              <TH className="text-right">Сумма</TH>
              <TH></TH>
            </TR>
          </THead>
          <TBody>
            {data.map((d) => (
              <TR key={d.id}>
                <TD className="whitespace-nowrap">
                  {new Date(d.document_date).toLocaleDateString("ru-RU")}
                </TD>
                <TD className="font-mono">{d.document_number ?? "—"}</TD>
                <TD>{branchName(d.branch_id)}</TD>
                <TD>{supplierName(d.supplier_id)}</TD>
                <TD>
                  <Badge tone={statusTone[d.status]}>{statusLabel[d.status]}</Badge>
                </TD>
                <TD className="text-right font-mono">
                  {Number(d.total_amount).toFixed(2)} {d.currency}
                </TD>
                <TD className="text-right">
                  <Link to="/incoming/$id" params={{ id: d.id }}>
                    <Button variant="ghost" size="sm">
                      Открыть
                    </Button>
                  </Link>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      <Modal open={creating} onClose={() => setCreating(false)} title="Новый приход">
        <NewIncomingForm onClose={() => setCreating(false)} />
      </Modal>
    </div>
  );
}
