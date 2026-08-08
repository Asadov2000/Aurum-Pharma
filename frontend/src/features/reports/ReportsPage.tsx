import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label, Modal, PageHeader, Select, SkeletonRows } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery, useTenantSettingsQuery } from "@/features/foundation/queries";
import { getZReportXlsx } from "@/features/pos/api";
import { downloadBlob } from "@/lib/download";

import { getStockOnDateXlsx } from "./api";
import { calendarDateInTimeZone, DEFAULT_REPORT_TIME_ZONE } from "./calendar";
import { useZReportQuery } from "./queries";
import { SalesOverviewPanel } from "./SalesOverviewPanel";
import { ShiftHistoryPanel } from "./ShiftHistoryPanel";
import { type ShiftHistoryItem } from "./types";
import { ZReportCard } from "./ZReportCard";

const stockFilterSchema = z.object({
  date: z.string().min(1, "Укажите дату"),
  branch_id: z.string(),
});

type StockFilterValues = z.infer<typeof stockFilterSchema>;

export function ReportsPage(): JSX.Element {
  const [selectedShift, setSelectedShift] = useState<ShiftHistoryItem | null>(null);
  const settings = useTenantSettingsQuery();
  const reportTimezone = settings.data?.report_timezone ?? DEFAULT_REPORT_TIME_ZONE;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Отчёты"
        description="Продажи, кассовые смены и складские остатки в одном рабочем пространстве."
      />

      <SalesOverviewPanel
        key={`sales-overview-${reportTimezone}`}
        reportTimezone={reportTimezone}
      />

      <ShiftHistoryPanel
        key={`shift-history-${reportTimezone}`}
        selectedShift={selectedShift}
        onSelect={setSelectedShift}
        reportTimezone={reportTimezone}
      />

      <StockOnDateCard key={`stock-date-${reportTimezone}`} reportTimezone={reportTimezone} />

      <Modal
        open={selectedShift !== null}
        onClose={() => setSelectedShift(null)}
        title={selectedShift ? `Z-отчёт · ${selectedShift.register_name}` : "Z-отчёт"}
        className="max-w-5xl"
      >
        {selectedShift && <ZReportSection shift={selectedShift} reportTimezone={reportTimezone} />}
      </Modal>
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
  const { data, isLoading, error, refetch, isFetching } = useZReportQuery(shift.id);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const onDownload = async () => {
    setDownloadError(null);
    setDownloading(true);
    try {
      const blob = await getZReportXlsx(shift.id);
      downloadBlob(blob, `z-report-${shift.id}.xlsx`);
    } catch (downloadFailure) {
      setDownloadError(describeApiError(downloadFailure, "Не удалось скачать Z-отчёт"));
    } finally {
      setDownloading(false);
    }
  };

  if (isLoading) return <SkeletonRows rows={4} />;
  if (error) {
    return (
      <div
        className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
        role="alert"
      >
        <p>{describeApiError(error, "Не удалось загрузить Z-отчёт")}</p>
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
    );
  }
  if (!data) return <p className="text-sm text-foreground-muted">Данных по смене нет.</p>;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-foreground-muted">
          {shift.branch_name} · {shift.cashier_name ?? "Кассир не указан"}
        </p>
        <Button variant="secondary" onClick={() => void onDownload()} isLoading={downloading}>
          Скачать XLSX
        </Button>
      </div>
      {downloadError && (
        <p
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          {downloadError}
        </p>
      )}
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

export function StockOnDateCard({
  reportTimezone = DEFAULT_REPORT_TIME_ZONE,
}: {
  reportTimezone?: string;
}): JSX.Element {
  const branches = useBranchesQuery(false);
  const defaults = useMemo<StockFilterValues>(
    () => ({ date: calendarDateInTimeZone(new Date(), reportTimezone), branch_id: "" }),
    [reportTimezone],
  );
  const form = useForm<StockFilterValues>({ defaultValues: defaults });
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasBranches = Boolean(branches.data && branches.data.length > 0);

  const onDownload = form.handleSubmit(async (values) => {
    const parsed = stockFilterSchema.safeParse(values);
    if (!parsed.success) {
      form.setError("date", {
        type: "validate",
        message: parsed.error.issues[0]?.message ?? "Проверьте дату",
      });
      return;
    }
    setError(null);
    setDownloading(true);
    try {
      const blob = await getStockOnDateXlsx(parsed.data.date, parsed.data.branch_id || undefined);
      downloadBlob(blob, `stock-${parsed.data.date}.xlsx`);
    } catch (downloadFailure) {
      setError(describeApiError(downloadFailure, "Не удалось скачать отчёт по остаткам"));
    } finally {
      setDownloading(false);
    }
  });

  return (
    <section
      className="flex flex-col gap-4 rounded-lg border border-border bg-surface px-4 py-4 lg:flex-row lg:items-end lg:justify-between"
      aria-labelledby="stock-report-title"
    >
      <div className="max-w-xl">
        <h2 id="stock-report-title" className="text-lg font-semibold text-foreground">
          Остатки на дату
        </h2>
        <p className="mt-1 text-sm text-foreground-muted">
          Снимок количества и закупочной стоимости партий для бухгалтерской сверки.
        </p>
      </div>
      <form
        className="flex w-full flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end lg:w-auto lg:justify-end"
        onSubmit={(event) => void onDownload(event)}
      >
        <div className="w-full sm:w-auto">
          <Label htmlFor="stock_date">Дата</Label>
          <Input id="stock_date" type="date" {...form.register("date")} />
          {form.formState.errors.date && (
            <p className="mt-1 text-xs text-danger">{form.formState.errors.date.message}</p>
          )}
        </div>
        {hasBranches && (
          <div className="w-full sm:w-auto">
            <Label htmlFor="stock_branch">Точка</Label>
            <Select id="stock_branch" className="w-full sm:w-56" {...form.register("branch_id")}>
              <option value="">Все доступные</option>
              {branches.data?.map((branch) => (
                <option key={branch.id} value={branch.id}>
                  {branch.name}
                </option>
              ))}
            </Select>
          </div>
        )}
        <Button
          type="submit"
          variant="secondary"
          className="w-full sm:w-auto"
          isLoading={downloading}
        >
          Скачать остатки XLSX
        </Button>
        {(error || branches.error) && (
          <p className="w-full text-sm text-danger" role="alert">
            {error ?? describeApiError(branches.error, "Не удалось загрузить точки")}
          </p>
        )}
      </form>
    </section>
  );
}
