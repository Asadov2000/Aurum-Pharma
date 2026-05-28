import { useState } from "react";

import {
  Badge,
  Button,
  Input,
  Label,
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
import { useAuth } from "@/features/auth/hooks";
import { useBranchesQuery, useRegistersQuery } from "@/features/foundation/queries";
import { paymentMethodLabel } from "@/features/pos/labels";
import { type PaymentMethod } from "@/features/pos/types";
import { describeApiError } from "@/lib/errorMessages";

import { SaleDetailModal } from "./SaleDetailModal";
import { useSalesQuery } from "./queries";
import { type SaleListItem } from "./types";

const PAGE_SIZE = 50;

export function SalesPage(): JSX.Element {
  const { user } = useAuth();
  const canFilterByLocation = Boolean(
    user?.is_developer || user?.is_administrator || user?.home_tenant_id,
  );

  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [receipt, setReceipt] = useState("");
  const [branchId, setBranchId] = useState("");
  const [registerId, setRegisterId] = useState("");
  const [hasRefund, setHasRefund] = useState<"" | "true" | "false">("");
  const [page, setPage] = useState(1);
  const [openRow, setOpenRow] = useState<SaleListItem | null>(null);

  const branches = useBranchesQuery(true, canFilterByLocation);
  const registers = useRegistersQuery(null, false, canFilterByLocation);

  const { data, isLoading, error } = useSalesQuery({
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    receipt_number: receipt || undefined,
    branch_id: branchId || undefined,
    register_id: registerId || undefined,
    has_refund: hasRefund === "" ? undefined : hasRefund === "true",
    page,
    page_size: PAGE_SIZE,
  });

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const resetPage = () => setPage(1);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Чеки</h1>
        <span className="text-sm text-foreground-muted">всего: {total}</span>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-md border border-border bg-surface p-3">
        <div>
          <Label htmlFor="receipt">№ чека</Label>
          <Input
            id="receipt"
            value={receipt}
            onChange={(e) => {
              setReceipt(e.target.value);
              resetPage();
            }}
            placeholder="000142"
            className="w-32"
          />
        </div>
        <div>
          <Label htmlFor="date_from">С</Label>
          <Input
            id="date_from"
            type="date"
            value={dateFrom}
            onChange={(e) => {
              setDateFrom(e.target.value);
              resetPage();
            }}
          />
        </div>
        <div>
          <Label htmlFor="date_to">По</Label>
          <Input
            id="date_to"
            type="date"
            value={dateTo}
            onChange={(e) => {
              setDateTo(e.target.value);
              resetPage();
            }}
          />
        </div>
        {canFilterByLocation && (
          <>
            <div>
              <Label htmlFor="branch">Точка</Label>
              <Select
                id="branch"
                value={branchId}
                onChange={(e) => {
                  setBranchId(e.target.value);
                  resetPage();
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
              <Label htmlFor="register">Касса</Label>
              <Select
                id="register"
                value={registerId}
                onChange={(e) => {
                  setRegisterId(e.target.value);
                  resetPage();
                }}
                className="w-44"
              >
                <option value="">Все</option>
                {registers.data?.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </Select>
            </div>
          </>
        )}
        <div>
          <Label htmlFor="has_refund">Возвраты</Label>
          <Select
            id="has_refund"
            value={hasRefund}
            onChange={(e) => {
              setHasRefund(e.target.value as "" | "true" | "false");
              resetPage();
            }}
            className="w-40"
          >
            <option value="">Все</option>
            <option value="true">С возвратом</option>
            <option value="false">Без возврата</option>
          </Select>
        </div>
      </div>

      {error && (
        <p className="text-sm text-danger">
          {describeApiError(error, "Не удалось загрузить чеки")}
        </p>
      )}

      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : !data || data.items.length === 0 ? (
        <TableEmpty title="Чеков пока нет">
          {receipt || dateFrom || dateTo || branchId || registerId || hasRefund
            ? "Измените фильтры поиска."
            : "Завершённые продажи появятся здесь — отсюда же оформляется возврат."}
        </TableEmpty>
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>№</TH>
                <TH>Дата</TH>
                <TH>Кассир</TH>
                <TH className="text-right">Сумма</TH>
                <TH>Оплата</TH>
                <TH>Статус</TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((s) => (
                <TR key={s.id} className="cursor-pointer" onClick={() => setOpenRow(s)}>
                  <TD className="font-mono">{s.receipt_number ?? "—"}</TD>
                  <TD className="whitespace-nowrap">
                    {s.completed_at
                      ? new Date(s.completed_at).toLocaleString("ru-RU")
                      : "—"}
                  </TD>
                  <TD>{s.cashier_name ?? "—"}</TD>
                  <TD className="text-right font-mono">
                    {Number(s.total_amount).toFixed(2)} {s.currency}
                  </TD>
                  <TD className="text-xs text-foreground-secondary">
                    {s.payment_methods
                      .map((m) => paymentMethodLabel[m as PaymentMethod] ?? m)
                      .join(", ") || "—"}
                  </TD>
                  <TD>
                    <div className="flex flex-wrap gap-1">
                      {s.is_refund ? (
                        <Badge tone="warning">Возврат</Badge>
                      ) : (
                        <Badge tone="success">Продажа</Badge>
                      )}
                      {s.has_refund && <Badge tone="info">Есть возврат</Badge>}
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>

          {totalPages > 1 && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-foreground-muted">
                Страница {page} из {totalPages}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page === 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  ←
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  →
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {openRow && (
        <SaleDetailModal row={openRow} onClose={() => setOpenRow(null)} />
      )}
    </div>
  );
}
