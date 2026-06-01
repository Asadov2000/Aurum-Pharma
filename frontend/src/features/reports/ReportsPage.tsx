import { useEffect, useState } from "react";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Select,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";
import { getZReportXlsx } from "@/features/pos/api";
import { downloadBlob } from "@/lib/download";

import { getSalesSummaryXlsx, getStockOnDateXlsx } from "./api";
import { useZReportQuery } from "./queries";
import { ZReportCard } from "./ZReportCard";

const LAST_CLOSED_KEY = "pos:lastClosedShiftId";

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function ReportsPage(): JSX.Element {
  const [input, setInput] = useState("");
  const [shiftId, setShiftId] = useState<string | null>(null);
  const lastClosed = window.localStorage.getItem(LAST_CLOSED_KEY);

  useEffect(() => {
    if (lastClosed && !input && !shiftId) setInput(lastClosed);
  }, [lastClosed, input, shiftId]);

  const onLoad = () => {
    const trimmed = input.trim();
    setShiftId(trimmed === "" ? null : trimmed);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">Отчёты</h1>

      <Card>
        <CardHeader>
          <CardTitle>Z-отчёт по смене</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-96 max-w-full">
              <Label htmlFor="shift_id">ID смены</Label>
              <Input
                id="shift_id"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="UUID смены"
              />
              {lastClosed && lastClosed !== shiftId && (
                <p className="mt-1 text-xs text-foreground-muted">
                  Подставлен последний закрытый shift_id из этого браузера.
                </p>
              )}
            </div>
            <Button onClick={onLoad} disabled={!input.trim()}>
              Загрузить
            </Button>
          </div>
        </CardContent>
      </Card>

      {shiftId && <ZReportSection shiftId={shiftId} />}

      <SalesSummaryCard />

      <StockOnDateCard />

      <Card>
        <CardHeader>
          <CardTitle>Другие отчёты</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-foreground-secondary">
            Отчёт по списаниям появится отдельно — для него нужен новый серверный
            эндпоинт.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function ZReportSection({ shiftId }: { shiftId: string }): JSX.Element {
  const { data, isLoading, error } = useZReportQuery(shiftId);
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);

  const onDownload = async () => {
    setDlError(null);
    setDownloading(true);
    try {
      const blob = await getZReportXlsx(shiftId);
      downloadBlob(blob, `z-report-${shiftId}.xlsx`);
    } catch (err) {
      setDlError(describeApiError(err, "Не удалось скачать Z-отчёт"));
    } finally {
      setDownloading(false);
    }
  };

  if (isLoading) return <p className="text-sm text-foreground-muted">Загрузка…</p>;
  if (error) {
    return (
      <p className="text-sm text-danger">
        {describeApiError(error, "Не удалось загрузить отчёт")}
      </p>
    );
  }
  if (!data) return <p className="text-sm text-foreground-muted">Нет данных</p>;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => void onDownload()} isLoading={downloading}>
          Скачать Z-отчёт (XLSX)
        </Button>
      </div>
      {dlError && <p className="text-sm text-danger">{dlError}</p>}
      <ZReportCard report={data} />
    </div>
  );
}

function SalesSummaryCard(): JSX.Element {
  const branches = useBranchesQuery(false);
  const [from, setFrom] = useState(() => isoDate(new Date(new Date().getFullYear(), new Date().getMonth(), 1)));
  const [to, setTo] = useState(() => isoDate(new Date()));
  const [branchId, setBranchId] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasBranches = Boolean(branches.data && branches.data.length > 0);

  const onDownload = async () => {
    setError(null);
    if (!from || !to) {
      setError("Укажите период");
      return;
    }
    if (from > to) {
      setError("Начало периода позже конца");
      return;
    }
    setDownloading(true);
    try {
      const blob = await getSalesSummaryXlsx(from, to, branchId || undefined);
      downloadBlob(blob, `sales-summary-${from}_${to}.xlsx`);
    } catch (err) {
      setError(describeApiError(err, "Не удалось скачать сводный отчёт"));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Сводный отчёт по продажам</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label htmlFor="summary_from">С</Label>
            <Input
              id="summary_from"
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="summary_to">По</Label>
            <Input
              id="summary_to"
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
          {hasBranches && (
            <div>
              <Label htmlFor="summary_branch">Филиал</Label>
              <Select
                id="summary_branch"
                value={branchId}
                onChange={(e) => setBranchId(e.target.value)}
                className="w-56"
              >
                <option value="">Все филиалы</option>
                {branches.data?.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </Select>
            </div>
          )}
          <Button variant="secondary" onClick={() => void onDownload()} isLoading={downloading}>
            Скачать сводный отчёт (XLSX)
          </Button>
        </div>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      </CardContent>
    </Card>
  );
}

export function StockOnDateCard(): JSX.Element {
  const branches = useBranchesQuery(false);
  const [date, setDate] = useState(() => isoDate(new Date()));
  const [branchId, setBranchId] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasBranches = Boolean(branches.data && branches.data.length > 0);

  const onDownload = async () => {
    setError(null);
    if (!date) {
      setError("Укажите дату");
      return;
    }
    setDownloading(true);
    try {
      const blob = await getStockOnDateXlsx(date, branchId || undefined);
      downloadBlob(blob, `stock-${date}.xlsx`);
    } catch (err) {
      setError(describeApiError(err, "Не удалось скачать отчёт по остаткам"));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Остатки на дату</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label htmlFor="stock_date">Дата</Label>
            <Input
              id="stock_date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </div>
          {hasBranches && (
            <div>
              <Label htmlFor="stock_branch">Филиал</Label>
              <Select
                id="stock_branch"
                value={branchId}
                onChange={(e) => setBranchId(e.target.value)}
                className="w-56"
              >
                <option value="">Все филиалы</option>
                {branches.data?.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </Select>
            </div>
          )}
          <Button variant="secondary" onClick={() => void onDownload()} isLoading={downloading}>
            Скачать отчёт по остаткам (XLSX)
          </Button>
        </div>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      </CardContent>
    </Card>
  );
}
