import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
  SegmentedControl,
  Select,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";
import { downloadBlob } from "@/lib/download";

import { getSalesSummaryXlsx } from "./api";
import { addCalendarDays, calendarDateInTimeZone, currentReportMonthRange } from "./calendar";
import { useSalesSummaryQuery } from "./queries";
import {
  type ReportPaymentBreakdown,
  type SalesSummaryOverview,
  type SalesSummaryParams,
} from "./types";

const filterSchema = z
  .object({
    from: z.string().min(1, "Укажите начало периода"),
    to: z.string().min(1, "Укажите конец периода"),
    branch_id: z.string(),
  })
  .refine(({ from, to }) => from <= to, {
    message: "Начало периода позже конца",
    path: ["to"],
  })
  .refine(({ from, to }) => !from || !to || to <= addCalendarDays(from, 365), {
    message: "Для экранной сводки выберите не более 366 дней",
    path: ["to"],
  });

type FilterValues = z.infer<typeof filterSchema>;
type PeriodPreset = "today" | "week" | "month" | "custom";

const PERIOD_OPTIONS = [
  { value: "today", label: "Сегодня" },
  { value: "week", label: "7 дней" },
  { value: "month", label: "Месяц" },
  { value: "custom", label: "Период" },
] as const;

const PAYMENT_LABELS: Array<[keyof ReportPaymentBreakdown, string]> = [
  ["cash", "Наличные"],
  ["card", "Карта"],
  ["qr", "QR-код"],
  ["bank_transfer", "Банковский перевод"],
  ["mixed", "Смешанная оплата"],
];

export function SalesOverviewPanel({ reportTimezone }: { reportTimezone: string }): JSX.Element {
  const filterPreferenceKey = useFilterPreferenceKey("reports-sales-overview");
  const defaults = useMemo(() => currentReportMonthRange(reportTimezone), [reportTimezone]);
  const initialValues = useMemo<FilterValues>(
    () => ({ from: defaults.dateFrom, to: defaults.dateTo, branch_id: "" }),
    [defaults],
  );
  const form = useForm<FilterValues>({ defaultValues: initialValues });
  const watched = form.watch();
  const [preset, setPreset] = useState<PeriodPreset>("month");
  const [params, setParams] = useState<SalesSummaryParams>(() => toParams(initialValues));
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const branches = useBranchesQuery(false);
  const query = useSalesSummaryQuery(params);

  const setValidatedParams = (values: FilterValues): boolean => {
    const parsed = filterSchema.safeParse(values);
    if (!parsed.success) {
      form.setError("to", {
        type: "validate",
        message: parsed.error.issues[0]?.message ?? "Проверьте период",
      });
      return false;
    }
    form.clearErrors();
    setParams(toParams(parsed.data));
    return true;
  };

  const applyFilters = form.handleSubmit((values) => {
    void setValidatedParams(values);
  });

  const applyPreset = (nextPreset: PeriodPreset) => {
    setPreset(nextPreset);
    if (nextPreset === "custom") return;
    const today = calendarDateInTimeZone(new Date(), reportTimezone);
    const range =
      nextPreset === "today"
        ? { dateFrom: today, dateTo: today }
        : nextPreset === "week"
          ? { dateFrom: addCalendarDays(today, -6), dateTo: today }
          : currentReportMonthRange(reportTimezone);
    form.setValue("from", range.dateFrom);
    form.setValue("to", range.dateTo);
    form.clearErrors();
    setParams(
      toParams({ from: range.dateFrom, to: range.dateTo, branch_id: form.getValues("branch_id") }),
    );
  };

  const resetFilters = () => {
    form.reset(initialValues);
    setPreset("month");
    setParams(toParams(initialValues));
    setDownloadError(null);
  };

  const onDownload = async () => {
    const values = form.getValues();
    if (!setValidatedParams(values)) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const blob = await getSalesSummaryXlsx(values.from, values.to, values.branch_id || undefined);
      downloadBlob(blob, `sales-summary-${values.from}_${values.to}.xlsx`);
    } catch (error) {
      setDownloadError(describeApiError(error, "Не удалось скачать сводный отчёт"));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section className="space-y-3" aria-labelledby="sales-overview-title">
      <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="sales-overview-title" className="text-lg font-semibold text-foreground">
            Продажи за период
          </h2>
          <p className="mt-0.5 text-sm text-foreground-muted">
            Оборот, возвраты и способы оплаты по доступным точкам.
          </p>
        </div>
        {query.data && (
          <p className="text-sm text-foreground-muted" aria-live="polite">
            {formatPeriod(query.data.date_from, query.data.date_to)}
            {query.data.branch_name ? ` · ${query.data.branch_name}` : " · все точки"}
            {query.isFetching && !query.isLoading ? " · обновление" : ""}
          </p>
        )}
      </div>

      <form onSubmit={(event) => void applyFilters(event)}>
        <ConfigurableFilterBar
          preferenceKey={filterPreferenceKey}
          filters={[
            {
              id: "period",
              label: "Период",
              content: (
                <div className="space-y-2">
                  <SegmentedControl
                    value={preset}
                    options={PERIOD_OPTIONS}
                    onChange={applyPreset}
                    label="Быстрый выбор периода"
                    size="sm"
                    className="w-full sm:w-auto"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label htmlFor="sales_summary_from">С</Label>
                      <Input
                        id="sales_summary_from"
                        type="date"
                        {...form.register("from", { onChange: () => setPreset("custom") })}
                      />
                    </div>
                    <div>
                      <Label htmlFor="sales_summary_to">По</Label>
                      <Input
                        id="sales_summary_to"
                        type="date"
                        {...form.register("to", { onChange: () => setPreset("custom") })}
                      />
                    </div>
                  </div>
                  {form.formState.errors.to && (
                    <p className="text-xs text-danger">{form.formState.errors.to.message}</p>
                  )}
                </div>
              ),
              active: watched.from !== initialValues.from || watched.to !== initialValues.to,
              onClear: () => applyPreset("month"),
              alwaysVisible: true,
            },
            {
              id: "branch",
              label: "Точка",
              content: (
                <div>
                  <Label htmlFor="sales_summary_branch">Точка</Label>
                  <Select
                    id="sales_summary_branch"
                    className="w-full sm:w-56"
                    {...form.register("branch_id")}
                  >
                    <option value="">Все доступные</option>
                    {branches.data?.map((branch) => (
                      <option key={branch.id} value={branch.id}>
                        {branch.name}
                      </option>
                    ))}
                  </Select>
                </div>
              ),
              active: Boolean(watched.branch_id),
              onClear: () => {
                form.setValue("branch_id", "");
                setParams((current) => ({ ...current, branch_id: undefined }));
              },
              defaultVisible: true,
            },
          ]}
          onResetValues={resetFilters}
          actions={
            <div className="flex w-full flex-wrap gap-2 sm:w-auto">
              <Button type="submit" className="flex-1 sm:flex-none">
                Обновить сводку
              </Button>
              <Button
                type="button"
                variant="secondary"
                className="flex-1 sm:flex-none"
                isLoading={downloading}
                onClick={() => void onDownload()}
              >
                Скачать XLSX
              </Button>
            </div>
          }
        />
      </form>

      {(branches.error || downloadError) && (
        <div role="alert" className="text-sm text-danger">
          {downloadError ?? describeApiError(branches.error, "Не удалось загрузить точки")}
        </div>
      )}

      {query.isLoading ? (
        <SkeletonRows rows={4} />
      ) : query.error ? (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-4 py-4 text-sm text-danger-foreground"
        >
          <p>{describeApiError(query.error, "Не удалось загрузить отчёт")}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            isLoading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : query.data ? (
        <OverviewContent data={query.data} />
      ) : null}
    </section>
  );
}

function OverviewContent({ data }: { data: SalesSummaryOverview }): JSX.Element {
  const hasSales = data.sales_count > 0 || data.returns_count > 0;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="grid grid-cols-2 gap-px border-b border-border bg-border lg:grid-cols-4">
        <Metric label="Чистая выручка" value={formatMoney(data.net, data.currency)} emphasis />
        <Metric label="Продажи" value={formatMoney(data.gross_sales, data.currency)} />
        <Metric label="Средний чек" value={formatMoney(data.average_sale, data.currency)} />
        <Metric
          label="Возвраты"
          value={formatMoney(data.total_refunds, data.currency)}
          tone={Number(data.total_refunds) > 0 ? "danger" : "default"}
        />
      </div>

      {!hasSales ? (
        <div className="p-4">
          <TableEmpty title="За выбранный период операций нет">
            Выберите другой период или точку.
          </TableEmpty>
        </div>
      ) : (
        <div className="grid min-w-0 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
          <PaymentBreakdown data={data} />
          <DailyTrend data={data} />
        </div>
      )}

      <div className="grid grid-cols-3 border-t border-border bg-foreground/[0.015] text-sm">
        <FootMetric label="Чеков" value={data.sales_count.toLocaleString("ru-RU")} />
        <FootMetric label="Возвратов" value={data.returns_count.toLocaleString("ru-RU")} />
        <FootMetric label="Скидки" value={formatMoney(data.total_discounts, data.currency)} />
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  emphasis = false,
  tone = "default",
}: {
  label: string;
  value: string;
  emphasis?: boolean;
  tone?: "default" | "danger";
}): JSX.Element {
  return (
    <div className="min-w-0 bg-surface px-4 py-4 text-center">
      <p className="text-sm text-foreground-muted">{label}</p>
      <p
        className={`mt-1 truncate text-2xl font-semibold tabular-nums ${
          emphasis
            ? "text-success-foreground"
            : tone === "danger"
              ? "text-danger"
              : "text-foreground"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

function PaymentBreakdown({ data }: { data: SalesSummaryOverview }): JSX.Element {
  const rows = PAYMENT_LABELS.map(([key, label]) => ({
    key,
    label,
    amount: Number(data.payment_breakdown[key]),
  }));
  const max = Math.max(...rows.map((row) => row.amount), 1);
  const total = rows.reduce((sum, row) => sum + row.amount, 0);
  return (
    <section className="min-w-0 border-b border-border p-4 lg:border-b-0 lg:border-r">
      <h3 className="text-sm font-semibold text-foreground">Способы оплаты</h3>
      <div className="mt-3 space-y-3">
        {rows.map((row) => (
          <div key={row.key}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="truncate text-foreground-secondary">{row.label}</span>
              <span className="shrink-0 text-right tabular-nums">
                <span className="font-medium">
                  {formatMoney(String(row.amount), data.currency)}
                </span>
                <span className="ml-2 text-xs text-foreground-muted">
                  {total > 0 ? `${Math.round((row.amount / total) * 100)}%` : "0%"}
                </span>
              </span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-foreground/10">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${Math.max(row.amount > 0 ? 3 : 0, (row.amount / max) * 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function DailyTrend({ data }: { data: SalesSummaryOverview }): JSX.Element {
  const rows = data.daily.slice(-14);
  const max = Math.max(...rows.map((row) => Math.abs(Number(row.net))), 1);
  return (
    <section className="min-w-0 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">Динамика по дням</h3>
        {data.daily.length > rows.length && (
          <span className="text-xs text-foreground-muted">
            последние {rows.length} дней с операциями
          </span>
        )}
      </div>
      <div className="mt-3 space-y-2">
        {rows.map((row) => {
          const net = Number(row.net);
          return (
            <div
              key={row.day}
              className="grid min-w-0 grid-cols-[4.5rem_minmax(3rem,1fr)_auto] items-center gap-2 text-sm"
            >
              <span className="text-xs text-foreground-muted">{formatShortDate(row.day)}</span>
              <div className="h-2 overflow-hidden rounded-full bg-foreground/10">
                <div
                  className={`h-full rounded-full ${net < 0 ? "bg-danger" : "bg-success"}`}
                  style={{ width: `${Math.max(net === 0 ? 0 : 3, (Math.abs(net) / max) * 100)}%` }}
                />
              </div>
              <div className="min-w-24 text-right">
                <p className="font-mono tabular-nums">{formatMoney(row.net, data.currency)}</p>
                <p className="text-xs text-foreground-muted">{row.sales_count} чек.</p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function FootMetric({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="min-w-0 border-r border-border px-3 py-2.5 last:border-r-0">
      <p className="truncate text-xs text-foreground-muted">{label}</p>
      <p className="mt-0.5 truncate font-mono font-medium tabular-nums text-foreground">{value}</p>
    </div>
  );
}

function toParams(values: FilterValues): SalesSummaryParams {
  return {
    from: values.from,
    to: values.to,
    branch_id: values.branch_id || undefined,
  };
}

function formatMoney(value: string, currency: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return `0,00 ${currency}`;
  return `${amount.toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`;
}

function formatPeriod(from: string, to: string): string {
  return `${formatShortDate(from)} — ${formatShortDate(to)}`;
}

function formatShortDate(value: string): string {
  const [year = "", month = "", day = ""] = value.split("-");
  return `${day}.${month}.${year.slice(-2)}`;
}
