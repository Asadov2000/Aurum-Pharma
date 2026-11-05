import { hashKey } from "@tanstack/react-query";
import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import { Button, Modal, PageHeader, SkeletonRows } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermissionForBranch } from "@/features/auth/branchPermissions";
import { hasPermission } from "@/features/auth/permissions";
import { describeApiError } from "@/features/foundation/errors";
import { useTenantOperationalSettingsQuery } from "@/features/foundation/queries";
import { getZReportXlsx } from "@/features/pos/api";
import { downloadBlob } from "@/lib/download";
import { cn } from "@/lib/utils";
import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

import { useReportBranchScope } from "./branchScope";
import { DEFAULT_REPORT_TIME_ZONE } from "./calendar";
import { useZReportQuery, zReportAccessKey } from "./queries";
import { SalesOverviewPanel } from "./SalesOverviewPanel";
import { ShiftHistoryPanel } from "./ShiftHistoryPanel";
import { GrossProfitPanel } from "./GrossProfitPanel";
import { QuarantineOnDatePanel } from "./QuarantineOnDatePanel";
import { StockOnDatePanel } from "./StockOnDatePanel";
import { TopProductsPanel } from "./TopProductsPanel";
import { type ShiftHistoryItem } from "./types";
import { ZReportCard } from "./ZReportCard";

type ReportView = "sales" | "products" | "stock" | "shifts" | "quarantine" | "profit";

const REPORT_VIEW_KEY = "aurum:reports:view:v1";
const LAST_CLOSED_SHIFT_KEY = "pos:lastClosedShiftId";

const REPORT_VIEWS: Array<{ value: ReportView; label: string; description: string }> = [
  { value: "sales", label: "Продажи", description: "Выручка и оплаты" },
  { value: "products", label: "Товары", description: "Лидеры продаж" },
  { value: "stock", label: "Остатки", description: "Склад и сроки" },
  { value: "quarantine", label: "Карантин", description: "Неразрешённые возвраты" },
  { value: "profit", label: "Прибыль", description: "Валовая прибыль" },
  { value: "shifts", label: "Смены", description: "Итоги кассовых смен" },
];

export function ReportsPage(): JSX.Element {
  const { user } = useAuth();
  const { scopeKey, canView } = useReportBranchScope();
  const canExport = hasPermission(user, "reports.export");
  const sessionKey = hashKey([user?.id, user?.active_tenant_id, getAuthSessionGeneration()]);
  const [selection, setSelection] = useState<{
    shift: ShiftHistoryItem;
    sessionKey: string;
  } | null>(null);
  const selectedShift =
    selection?.sessionKey === sessionKey &&
    hasPermissionForBranch(user, "reports.view", selection.shift.branch_id)
      ? selection.shift
      : null;
  const setSelectedShift = useCallback(
    (shift: ShiftHistoryItem | null) => setSelection(shift ? { shift, sessionKey } : null),
    [sessionKey],
  );
  useEffect(() => {
    if (selection && !selectedShift) setSelection(null);
  }, [selection, selectedShift]);
  const [view, setView] = useState<ReportView>(readInitialReportView);
  const settings = useTenantOperationalSettingsQuery(canView);
  const reportTimezone = settings.data?.report_timezone ?? DEFAULT_REPORT_TIME_ZONE;

  const changeView = (next: ReportView) => {
    setView(next);
    writeReportView(next);
  };

  if (!canView)
    return (
      <AccessDeniedCard
        title="Отчёты"
        message="Нет доступа к отчётам аптечных точек."
        fallbackTo={null}
      />
    );

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
                key={`sales-overview-${reportTimezone}-${scopeKey}`}
                reportTimezone={reportTimezone}
                canExport={canExport}
              />
            ) : view === "profit" ? (
              <GrossProfitPanel
                key={`profit-${reportTimezone}-${scopeKey}-${user?.id}-${user?.active_tenant_id}`}
                reportTimezone={reportTimezone}
                canExport={canExport}
              />
            ) : view === "products" ? (
              <TopProductsPanel
                key={`top-products-${reportTimezone}-${scopeKey}`}
                reportTimezone={reportTimezone}
              />
            ) : view === "quarantine" ? (
              <QuarantineOnDatePanel
                key={`quarantine-${reportTimezone}-${scopeKey}-${user?.id}-${user?.active_tenant_id}`}
                reportTimezone={reportTimezone}
                canExport={canExport}
              />
            ) : view === "shifts" ? (
              <ShiftHistoryPanel
                key={`shift-history-${reportTimezone}-${scopeKey}-${sessionKey}`}
                selectedShift={selectedShift}
                onSelect={setSelectedShift}
                reportTimezone={reportTimezone}
              />
            ) : (
              <StockOnDatePanel
                key={`stock-date-${reportTimezone}-${scopeKey}`}
                reportTimezone={reportTimezone}
                canExport={canExport}
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
        {selectedShift && (
          <ZReportSection
            key={`${selectedShift.id}-${zReportAccessKey(user)}`}
            shift={selectedShift}
            reportTimezone={reportTimezone}
            canExport={hasPermissionForBranch(user, "reports.export", selectedShift.branch_id)}
          />
        )}
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
      className="grid grid-cols-2 overflow-hidden rounded-lg border border-border bg-surface md:grid-cols-6"
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
    return stored === "products" ||
      stored === "shifts" ||
      stored === "stock" ||
      stored === "quarantine" ||
      stored === "profit"
      ? stored
      : "sales";
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
  canExport,
}: {
  shift: ShiftHistoryItem;
  reportTimezone: string;
  canExport: boolean;
}): JSX.Element {
  const { user } = useAuth();
  const accessKey = zReportAccessKey(user);
  const { data, isLoading, error, refetch, isFetching } = useZReportQuery(
    shift.id,
    shift.branch_id,
  );
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const unsubscribeExport = useRef<(() => void) | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      unsubscribeExport.current?.();
      unsubscribeExport.current = null;
    };
  }, []);

  const onDownload = async () => {
    if (
      inFlight.current ||
      !canExport ||
      !mounted.current ||
      accessKey !== zReportAccessKey(useAuthStore.getState().user)
    )
      return;
    inFlight.current = true;
    let accessChanged = false;
    const unsubscribe = useAuthStore.subscribe((state) => {
      if (zReportAccessKey(state.user) !== accessKey) accessChanged = true;
    });
    unsubscribeExport.current = unsubscribe;
    const isCurrent = () =>
      mounted.current &&
      !accessChanged &&
      accessKey === zReportAccessKey(useAuthStore.getState().user);
    setDownloadError(null);
    setDownloading(true);
    try {
      const blob = await getZReportXlsx(shift.id);
      if (isCurrent()) downloadBlob(blob, `z-report-${shift.id}.xlsx`);
    } catch (downloadFailure) {
      if (isCurrent())
        setDownloadError(describeApiError(downloadFailure, "Не удалось скачать Z-отчёт"));
    } finally {
      unsubscribe();
      unsubscribeExport.current = null;
      inFlight.current = false;
      if (mounted.current) setDownloading(false);
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
        {canExport && (
          <Button variant="secondary" onClick={() => void onDownload()} isLoading={downloading}>
            Скачать XLSX
          </Button>
        )}
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
  canExport = false,
}: {
  reportTimezone?: string;
  canExport?: boolean;
}): JSX.Element {
  return <StockOnDatePanel reportTimezone={reportTimezone} canExport={canExport} />;
}
