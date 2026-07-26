import { useState } from "react";

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
import { useBranchesQuery, useTenantSettingsQuery } from "@/features/foundation/queries";
import { getZReportXlsx } from "@/features/pos/api";
import { downloadBlob } from "@/lib/download";

import { getSalesSummaryXlsx, getStockOnDateXlsx } from "./api";
import {
  calendarDateInTimeZone,
  currentReportMonthRange,
  DEFAULT_REPORT_TIME_ZONE,
} from "./calendar";
import { useZReportQuery } from "./queries";
import { ShiftHistoryPanel } from "./ShiftHistoryPanel";
import { type ShiftHistoryItem } from "./types";
import { ZReportCard } from "./ZReportCard";

export function ReportsPage(): JSX.Element {
  const [selectedShift, setSelectedShift] = useState<ShiftHistoryItem | null>(null);
  const settings = useTenantSettingsQuery();
  const reportTimezone = settings.data?.report_timezone ?? DEFAULT_REPORT_TIME_ZONE;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">Отчёты</h1>

      <ShiftHistoryPanel
        key={`shift-history-${reportTimezone}`}
        selectedShift={selectedShift}
        onSelect={setSelectedShift}
        reportTimezone={reportTimezone}
      />

      {selectedShift && (
        <ZReportSection shift={selectedShift} reportTimezone={reportTimezone} />
      )}

      <SalesSummaryCard key={`sales-summary-${reportTimezone}`} reportTimezone={reportTimezone} />

      <StockOnDateCard key={`stock-date-${reportTimezone}`} reportTimezone={reportTimezone} />

      <Card>
        <CardHeader>
          <CardTitle>Другие отчёты</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-foreground-secondary">
            Отчёт по списаниям появится отдельно — для него нужен новый серверный эндпоинт.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function ZReportSection({
  shift,
  reportTimezone,
}: {
  shift: ShiftHistoryItem;
  reportTimezone: string;
}): JSX.Element {
  const { data, isLoading, error } = useZReportQuery(shift.id);
  const [downloading, setDownloading] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);

  const onDownload = async () => {
    setDlError(null);
    setDownloading(true);
    try {
      const blob = await getZReportXlsx(shift.id);
      downloadBlob(blob, `z-report-${shift.id}.xlsx`);
    } catch (err) {
      setDlError(describeApiError(err, "Не удалось скачать Z-отчёт"));
    } finally {
      setDownloading(false);
    }
  };

  if (isLoading) return <p className="text-sm text-foreground-muted">Загрузка…</p>;
  if (error) {
    return (
      <p className="text-sm text-danger">{describeApiError(error, "Не удалось загрузить отчёт")}</p>
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
      <ZReportCard
        report={data}
        branchName={shift.branch_name}
        registerName={shift.register_name}
        cashierName={shift.cashier_name}
        reportTimezone={reportTimezone}
      />
    </div>
  );
}

function SalesSummaryCard({
  reportTimezone = DEFAULT_REPORT_TIME_ZONE,
}: {
  reportTimezone?: string;
}): JSX.Element {
  const branches = useBranchesQuery(false);
  const defaults = currentReportMonthRange(reportTimezone);
  const [from, setFrom] = useState(defaults.dateFrom);
  const [to, setTo] = useState(defaults.dateTo);
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
            <Input id="summary_to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
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

export function StockOnDateCard({
  reportTimezone = DEFAULT_REPORT_TIME_ZONE,
}: {
  reportTimezone?: string;
}): JSX.Element {
  const branches = useBranchesQuery(false);
  const [date, setDate] = useState(() =>
    calendarDateInTimeZone(new Date(), reportTimezone),
  );
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
