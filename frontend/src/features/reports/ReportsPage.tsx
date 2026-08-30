import { type KeyboardEvent, useState } from "react";

import { Button, Modal, PageHeader, SkeletonRows } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useTenantOperationalSettingsQuery } from "@/features/foundation/queries";
import { getZReportXlsx } from "@/features/pos/api";
import { downloadBlob } from "@/lib/download";
import { cn } from "@/lib/utils";

import { DEFAULT_REPORT_TIME_ZONE } from "./calendar";
import { useZReportQuery } from "./queries";
import { SalesOverviewPanel } from "./SalesOverviewPanel";
import { ShiftHistoryPanel } from "./ShiftHistoryPanel";
import { StockOnDatePanel } from "./StockOnDatePanel";
import { TopProductsPanel } from "./TopProductsPanel";
import { type ShiftHistoryItem } from "./types";
import { ZReportCard } from "./ZReportCard";

type ReportView = "sales" | "products" | "stock" | "shifts";

const REPORT_VIEW_KEY = "aurum:reports:view:v1";
const LAST_CLOSED_SHIFT_KEY = "pos:lastClosedShiftId";

const REPORT_VIEWS: Array<{ value: ReportView; label: string; description: string }> = [
  { value: "sales", label: "Продажи", description: "Выручка и оплаты" },
  { value: "products", label: "Товары", description: "Лидеры продаж" },
  { value: "stock", label: "Остатки", description: "Склад и сроки" },
  { value: "shifts", label: "Смены", description: "Итоги кассовых смен" },
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
            ) : view === "products" ? (
              <TopProductsPanel
                key={`top-products-${reportTimezone}`}
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
              <StockOnDatePanel
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
      className="grid grid-cols-2 overflow-hidden rounded-lg border border-border bg-surface md:grid-cols-4"
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
    return stored === "products" || stored === "shifts" || stored === "stock" ? stored : "sales";
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
  return <StockOnDatePanel reportTimezone={reportTimezone} />;
}
