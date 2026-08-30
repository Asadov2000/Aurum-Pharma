import { type KeyboardEvent, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Input, Label, Modal, PageHeader, Select, SkeletonRows } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery, useTenantOperationalSettingsQuery } from "@/features/foundation/queries";
import { getZReportXlsx } from "@/features/pos/api";
import { downloadBlob } from "@/lib/download";
import { cn } from "@/lib/utils";

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
type ReportView = "sales" | "shifts" | "stock";

const REPORT_VIEW_KEY = "aurum:reports:view:v1";
const LAST_CLOSED_SHIFT_KEY = "pos:lastClosedShiftId";

const REPORT_VIEWS: Array<{ value: ReportView; label: string; description: string }> = [
  { value: "sales", label: "Продажи", description: "Выручка и оплаты" },
  { value: "shifts", label: "Смены", description: "Итоги кассовых смен" },
  { value: "stock", label: "Остатки", description: "Склад на дату" },
];

export function ReportsPage(): JSX.Element {
  const [selectedShift, setSelectedShift] = useState<ShiftHistoryItem | null>(null);
  const [view, setView] = useState<ReportView>(readInitialReportView);
  const settings = useTenantOperationalSettingsQuery();
  const reportTimezone = settings.data?.report_timezone ?? DEFAULT_REPORT_TIME_ZONE;

  const changeView = (next: ReportView) => {
    setView(next);
    writeReportView(next);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Отчёты"
        description="Продажи, кассовые смены и складские остатки в одном рабочем пространстве."
        showTitleOnDesktop
      />

      {settings.isPending ? (
        <SkeletonRows rows={5} />
      ) : settings.isError || !settings.data ? (
        <div
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
          role="alert"
        >
          <p>
            {describeApiError(
              settings.error,
              "Не удалось загрузить часовой пояс отчётов. Даты и суммы пока не показаны.",
            )}
          </p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            isLoading={settings.isFetching}
            onClick={() => void settings.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : (
        <>
          <ReportTabs value={view} onChange={changeView} />

          <div id={`report-panel-${view}`} role="tabpanel" aria-labelledby={`report-tab-${view}`}>
            {view === "sales" ? (
              <SalesOverviewPanel
                key={`sales-overview-${reportTimezone}`}
                reportTimezone={reportTimezone}
              />
            ) : view === "shifts" ? (
              <ShiftHistoryPanel
                key={`shift-history-${reportTimezone}`}
                selectedShift={selectedShift}
                onSelect={setSelectedShift}
                reportTimezone={reportTimezone}
              />
            ) : (
              <StockOnDateCard
                key={`stock-date-${reportTimezone}`}
                reportTimezone={reportTimezone}
              />
            )}
          </div>
        </>
      )}

      <Modal
        open={selectedShift !== null}
        onClose={() => setSelectedShift(null)}
        title={
          selectedShift
            ? `Итог смены (Z-отчёт) · ${selectedShift.register_name}`
            : "Итог смены (Z-отчёт)"
        }
        className="max-w-5xl"
      >
        {selectedShift && <ZReportSection shift={selectedShift} reportTimezone={reportTimezone} />}
      </Modal>
    </div>
  );
}

function ReportTabs({
  value,
  onChange,
}: {
  value: ReportView;
  onChange: (value: ReportView) => void;
}): JSX.Element {
  const moveFocus = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % REPORT_VIEWS.length;
    if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + REPORT_VIEWS.length) % REPORT_VIEWS.length;
    }
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = REPORT_VIEWS.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    const nextView = REPORT_VIEWS[nextIndex];
    if (!nextView) return;
    onChange(nextView.value);
    window.requestAnimationFrame(() => {
      document.getElementById(`report-tab-${nextView.value}`)?.focus();
    });
  };

  return (
    <div
      className="grid grid-cols-3 overflow-hidden rounded-lg border border-border bg-surface"
      role="tablist"
      aria-label="Разделы отчётов"
    >
      {REPORT_VIEWS.map((option, index) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            id={`report-tab-${option.value}`}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={`report-panel-${option.value}`}
            tabIndex={active ? 0 : -1}
            className={cn(
              "relative min-w-0 border-r border-border px-3 py-3 text-left transition-colors duration-fast last:border-r-0 sm:px-5",
              active
                ? "bg-primary/[0.07] text-primary shadow-[inset_0_-3px_0_hsl(var(--primary))]"
                : "text-foreground-secondary hover:bg-foreground/[0.025] hover:text-foreground",
            )}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => moveFocus(event, index)}
          >
            <span className="block truncate text-sm font-semibold sm:text-base">
              {option.label}
            </span>
            <span
              className={cn(
                "mt-0.5 hidden truncate text-xs sm:block",
                active ? "text-primary/75" : "text-foreground-muted",
              )}
            >
              {option.description}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function readInitialReportView(): ReportView {
  if (typeof window === "undefined") return "sales";
  try {
    if (window.localStorage.getItem(LAST_CLOSED_SHIFT_KEY)) return "shifts";
    const stored = window.localStorage.getItem(REPORT_VIEW_KEY);
    return stored === "shifts" || stored === "stock" ? stored : "sales";
  } catch {
    return "sales";
  }
}

function writeReportView(view: ReportView): void {
  try {
    window.localStorage.setItem(REPORT_VIEW_KEY, view);
  } catch {
    // The report preference is optional; reporting must remain available.
  }
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
        currency={shift.currency}
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
            <Label htmlFor="stock_branch">Аптечная точка</Label>
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
          isLoading={downloading || branches.isPending}
          disabled={branches.isError}
        >
          Скачать остатки XLSX
        </Button>
        {(error || branches.error) && (
          <div
            className="flex w-full flex-wrap items-center gap-2 text-sm text-danger"
            role="alert"
          >
            <span>
              {error ?? describeApiError(branches.error, "Не удалось загрузить аптечные точки")}
            </span>
            {branches.error && (
              <Button variant="secondary" size="sm" onClick={() => void branches.refetch()}>
                Повторить
              </Button>
            )}
          </div>
        )}
      </form>
    </section>
  );
}
