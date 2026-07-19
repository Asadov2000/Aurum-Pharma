import { useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";

import {
  Badge,
  Button,
  FilterBar,
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
import { useBranchesQuery } from "@/features/foundation/queries";
import { describeApiError } from "@/features/foundation/errors";
import { useSuppliersQuery } from "@/features/suppliers/queries";

import { NewIncomingForm } from "./NewIncomingForm";
import { useIncomingListQuery } from "./queries";
import { statusLabel, statusOptions, statusTone } from "./labels";
import { type IncomingStatus } from "./types";

const PAGE_SIZE = 25;

export function IncomingPage(): JSX.Element {
  const [branchFilter, setBranchFilter] = useState<string>("");
  const [supplierFilter, setSupplierFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [documentNumberInput, setDocumentNumberInput] = useState("");
  const [documentNumber, setDocumentNumber] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);

  const branches = useBranchesQuery(true);
  const suppliers = useSuppliersQuery(true);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setDocumentNumber(documentNumberInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timeout);
  }, [documentNumberInput]);

  const params = useMemo(
    () => ({
      branch_id: branchFilter || undefined,
      supplier_id: supplierFilter || undefined,
      status: (statusFilter as IncomingStatus | "") || undefined,
      document_number: documentNumber.trim() || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [branchFilter, supplierFilter, statusFilter, documentNumber, dateFrom, dateTo, page],
  );
  const { data, isLoading, error } = useIncomingListQuery(params);
  const filtersActive = Boolean(
    branchFilter ||
      supplierFilter ||
      statusFilter ||
      documentNumberInput ||
      dateFrom ||
      dateTo,
  );

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

      <FilterBar>
        <div>
          <Label htmlFor="document_number_filter">Номер документа</Label>
          <Input
            id="document_number_filter"
            value={documentNumberInput}
            onChange={(e) => {
              setDocumentNumberInput(e.target.value);
            }}
            placeholder="Например, ПР-2401"
            className="w-44"
          />
        </div>
        <div>
          <Label htmlFor="branch_filter">Точка</Label>
          <Select
            id="branch_filter"
            value={branchFilter}
            onChange={(e) => {
              setBranchFilter(e.target.value);
              setPage(1);
            }}
            className="w-44"
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
            onChange={(e) => {
              setSupplierFilter(e.target.value);
              setPage(1);
            }}
            className="w-44"
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
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(1);
            }}
            className="w-36"
          >
            <option value="">Все</option>
            {statusOptions.map((s) => (
              <option key={s} value={s}>
                {statusLabel[s]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="date_from_filter">С даты</Label>
          <Input
            id="date_from_filter"
            type="date"
            value={dateFrom}
            max={dateTo || undefined}
            onChange={(e) => {
              setDateFrom(e.target.value);
              setPage(1);
            }}
            className="w-36"
          />
        </div>
        <div>
          <Label htmlFor="date_to_filter">По дату</Label>
          <Input
            id="date_to_filter"
            type="date"
            value={dateTo}
            min={dateFrom || undefined}
            onChange={(e) => {
              setDateTo(e.target.value);
              setPage(1);
            }}
            className="w-36"
          />
        </div>
        <Button
          variant="secondary"
          size="sm"
          disabled={!filtersActive}
          onClick={() => {
            setBranchFilter("");
            setSupplierFilter("");
            setStatusFilter("");
            setDocumentNumberInput("");
            setDocumentNumber("");
            setDateFrom("");
            setDateTo("");
            setPage(1);
          }}
        >
          Сбросить
        </Button>
      </FilterBar>

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить приходы")}
        </p>
      )}
      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : !data || data.items.length === 0 ? (
        <TableEmpty>
          {filtersActive ? "По текущим фильтрам ничего не найдено" : "Приходов пока нет"}
        </TableEmpty>
      ) : (
        <>
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
              {data.items.map((d) => (
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
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={data.total}
            onPage={setPage}
          />
        </>
      )}

      <Modal open={creating} onClose={() => setCreating(false)} title="Новый приход">
        <NewIncomingForm onClose={() => setCreating(false)} />
      </Modal>
    </div>
  );
}
