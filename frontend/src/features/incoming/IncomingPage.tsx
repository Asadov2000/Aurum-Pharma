import { useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";

import {
  Badge,
  Button,
  ConfigurableFilterBar,
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
import { hasAnyPermission, hasPermission } from "@/features/auth/permissions";
import { useBranchesQuery } from "@/features/foundation/queries";
import { describeApiError } from "@/features/foundation/errors";
import { SupplierPicker } from "@/features/suppliers/SupplierPicker";

import { NewIncomingForm } from "./NewIncomingForm";
import { useIncomingListQuery } from "./queries";
import { statusLabel, statusOptions, statusTone } from "./labels";
import { type IncomingStatus } from "./types";

const PAGE_SIZE = 25;

export function IncomingPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("incoming");
  const canCreate = hasPermission(user, "incoming.create");
  const canDiscoverBranches = hasAnyPermission(user, [
    "branches.view",
    "registers.view",
    "pos.shift_open",
    "pos.shift_close",
    "pos.sell",
    "incoming.view",
    "incoming.create",
  ]);
  const canViewSuppliers = hasPermission(user, "suppliers.view");
  const [branchFilter, setBranchFilter] = useState<string>("");
  const [supplierFilter, setSupplierFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [documentNumberInput, setDocumentNumberInput] = useState("");
  const [documentNumber, setDocumentNumber] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);

  const branches = useBranchesQuery(true, canDiscoverBranches);

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
  const { data, isLoading, isFetching, error, refetch } = useIncomingListQuery(params);
  const filtersActive = Boolean(
    branchFilter || supplierFilter || statusFilter || documentNumberInput || dateFrom || dateTo,
  );

  const branchName = (id: string) =>
    branches.data?.find((b) => b.id === id)?.name ?? id.slice(0, 8);
  return (
    <div className="space-y-4">
      <PageHeader
        title="Приходы"
        description="Черновики поставок, принятые документы и история движения товара на склад."
        actions={canCreate && <Button onClick={() => setCreating(true)}>+ Новый приход</Button>}
      />

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "document_number",
            label: "Номер документа",
            content: (
              <div>
                <Label htmlFor="document_number_filter">Номер документа</Label>
                <Input
                  id="document_number_filter"
                  value={documentNumberInput}
                  onChange={(e) => {
                    setDocumentNumberInput(e.target.value);
                  }}
                  placeholder="Например, ПР-2401"
                  className="w-full sm:w-44"
                />
              </div>
            ),
            active: Boolean(documentNumberInput),
            onClear: () => {
              setDocumentNumberInput("");
              setDocumentNumber("");
              setPage(1);
            },
            alwaysVisible: true,
          },
          {
            id: "branch",
            label: "Точка",
            content: (
              <div>
                <Label htmlFor="branch_filter">Точка</Label>
                <Select
                  id="branch_filter"
                  value={branchFilter}
                  onChange={(e) => {
                    setBranchFilter(e.target.value);
                    setPage(1);
                  }}
                  className="w-full sm:w-44"
                >
                  <option value="">Все</option>
                  {branches.data?.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
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
            available: canDiscoverBranches,
          },
          {
            id: "supplier",
            label: "Поставщик",
            content: (
              <div>
                <Label htmlFor="supplier_filter">Поставщик</Label>
                <SupplierPicker
                  id="supplier_filter"
                  value={supplierFilter}
                  onChange={(supplierId) => {
                    setSupplierFilter(supplierId);
                    setPage(1);
                  }}
                  className="w-full sm:w-52"
                  placeholder="Все поставщики"
                  clearable
                  includeInactive
                />
              </div>
            ),
            active: Boolean(supplierFilter),
            onClear: () => {
              setSupplierFilter("");
              setPage(1);
            },
            available: canViewSuppliers,
          },
          {
            id: "status",
            label: "Статус",
            content: (
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
            ),
            active: Boolean(statusFilter),
            onClear: () => {
              setStatusFilter("");
              setPage(1);
            },
            defaultVisible: true,
          },
          {
            id: "period",
            label: "Период",
            content: (
              <div className="grid w-full grid-cols-1 gap-2 sm:w-auto sm:grid-cols-2">
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
              </div>
            ),
            active: Boolean(dateFrom || dateTo),
            onClear: () => {
              setDateFrom("");
              setDateTo("");
              setPage(1);
            },
          },
        ]}
        onResetValues={() => {
          setBranchFilter("");
          setSupplierFilter("");
          setStatusFilter("");
          setDocumentNumberInput("");
          setDocumentNumber("");
          setDateFrom("");
          setDateTo("");
          setPage(1);
        }}
      />

      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : error ? (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
        >
          <p>{describeApiError(error, "Не удалось загрузить приходы")}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            isLoading={isFetching}
            onClick={() => void refetch()}
          >
            Повторить
          </Button>
        </div>
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
                  <TD>{d.supplier_name ?? d.supplier_id.slice(0, 8)}</TD>
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
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
        </>
      )}

      {canCreate && (
        <Modal open={creating} onClose={() => setCreating(false)} title="Новый приход">
          <NewIncomingForm onClose={() => setCreating(false)} />
        </Modal>
      )}
    </div>
  );
}
