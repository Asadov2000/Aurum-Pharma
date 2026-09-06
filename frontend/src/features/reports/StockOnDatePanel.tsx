import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  ConfigurableFilterBar,
  Input,
  Label,
  Pagination,
  Select,
  SkeletonRows,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  TableEmpty,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";
import { downloadBlob } from "@/lib/download";

import { getStockOnDateXlsx } from "./api";
import { calendarDateInTimeZone, DEFAULT_REPORT_TIME_ZONE } from "./calendar";
import { useStockOnDateQuery } from "./queries";
import { type StockOnDateParams } from "./types";

const filterSchema = z.object({
  date: z.string().min(1, "Укажите дату"),
  branch_id: z.string(),
  query: z.string().max(120),
  expires_within_days: z.string(),
});

type FilterValues = z.infer<typeof filterSchema>;
const PAGE_SIZE = 25;

export function StockOnDatePanel({
  reportTimezone = DEFAULT_REPORT_TIME_ZONE,
  canExport = false,
}: {
  reportTimezone?: string;
  canExport?: boolean;
}): JSX.Element {
  const filterPreferenceKey = useFilterPreferenceKey("reports-stock-on-date");
  const defaults = useMemo<FilterValues>(
    () => ({
      date: calendarDateInTimeZone(new Date(), reportTimezone),
      branch_id: "",
      query: "",
      expires_within_days: "",
    }),
    [reportTimezone],
  );
  const form = useForm<FilterValues>({ defaultValues: defaults });
  const watched = form.watch();
  const [params, setParams] = useState<StockOnDateParams>(() => toParams(defaults, 1));
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const branches = useBranchesQuery(false);
  const query = useStockOnDateQuery(params);
  const watchedParams = toParams(watched, params.page);
  const pendingChanges =
    watchedParams.date !== params.date ||
    watchedParams.branch_id !== params.branch_id ||
    watchedParams.query !== params.query ||
    watchedParams.expires_within_days !== params.expires_within_days;

  const apply = form.handleSubmit((values) => {
    const parsed = filterSchema.safeParse(values);
    if (!parsed.success) {
      form.setError("date", {
        type: "validate",
        message: parsed.error.issues[0]?.message ?? "Проверьте фильтры",
      });
      return;
    }
    form.clearErrors();
    setParams(toParams(parsed.data, 1));
  });

  const download = async () => {
    const parsed = filterSchema.safeParse(form.getValues());
    if (!parsed.success) return;
    setDownloadError(null);
    setDownloading(true);
    try {
      const blob = await getStockOnDateXlsx(parsed.data.date, parsed.data.branch_id || undefined);
      downloadBlob(blob, `stock-${parsed.data.date}.xlsx`);
    } catch (error) {
      setDownloadError(describeApiError(error, "Не удалось скачать полный отчёт"));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section className="space-y-3" aria-labelledby="stock-report-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="stock-report-title" className="text-lg font-semibold text-foreground">
            Остатки и сроки годности
          </h2>
          <p className="mt-0.5 text-sm text-foreground-muted">
            Остаток на выбранную дату. Фильтр срока помогает заранее увидеть риск просрочки.
          </p>
        </div>
        {canExport && (
          <Button variant="secondary" isLoading={downloading} onClick={() => void download()}>
            Скачать в Excel
          </Button>
        )}
      </div>

      <form onSubmit={(event) => void apply(event)}>
        <ConfigurableFilterBar
          preferenceKey={filterPreferenceKey}
          pendingChangesMessage={
            pendingChanges
              ? "Условия изменены. Нажмите «Показать», чтобы обновить отчёт."
              : undefined
          }
          filters={[
            {
              id: "search",
              label: "Поиск",
              activeLabel: watched.query,
              alwaysVisible: true,
              active: Boolean(watched.query),
              onClear: () => {
                form.setValue("query", "");
                setParams((current) => ({ ...current, query: undefined, page: 1 }));
              },
              content: (
                <div>
                  <Label htmlFor="stock_query">Товар, МНН или партия</Label>
                  <Input
                    id="stock_query"
                    placeholder="Например, парацетамол"
                    {...form.register("query")}
                  />
                </div>
              ),
            },
            {
              id: "date",
              label: "Дата остатка",
              alwaysVisible: true,
              activeLabel: watched.date || "Не указана",
              active: watched.date !== defaults.date,
              onClear: () => {
                form.setValue("date", defaults.date);
                form.clearErrors();
                setParams((current) => ({ ...current, date: defaults.date, page: 1 }));
              },
              content: (
                <div>
                  <Label htmlFor="stock_date">Дата остатка</Label>
                  <Input id="stock_date" type="date" {...form.register("date")} />
                  {form.formState.errors.date && (
                    <p className="mt-1 text-xs text-danger">{form.formState.errors.date.message}</p>
                  )}
                </div>
              ),
            },
            {
              id: "branch",
              label: "Аптечная точка",
              activeLabel: branches.data?.find((branch) => branch.id === watched.branch_id)?.name,
              active: Boolean(watched.branch_id),
              onClear: () => {
                form.setValue("branch_id", "");
                setParams((current) => ({ ...current, branch_id: undefined, page: 1 }));
              },
              content: (
                <div>
                  <Label htmlFor="stock_branch">Аптечная точка</Label>
                  <Select id="stock_branch" {...form.register("branch_id")}>
                    <option value="">Все доступные точки</option>
                    {branches.data?.map((branch) => (
                      <option key={branch.id} value={branch.id}>
                        {branch.name}
                      </option>
                    ))}
                  </Select>
                </div>
              ),
            },
            {
              id: "expiry",
              label: "Срок закончится",
              activeLabel:
                watched.expires_within_days === "0"
                  ? "Уже истёк"
                  : `В течение ${watched.expires_within_days} дней`,
              active: Boolean(watched.expires_within_days),
              onClear: () => {
                form.setValue("expires_within_days", "");
                setParams((current) => ({ ...current, expires_within_days: undefined, page: 1 }));
              },
              content: (
                <div>
                  <Label htmlFor="stock_expiry">Срок закончится</Label>
                  <Select id="stock_expiry" {...form.register("expires_within_days")}>
                    <option value="">Любой срок</option>
                    <option value="0">Уже истёк</option>
                    <option value="30">В течение 30 дней</option>
                    <option value="90">В течение 3 месяцев</option>
                    <option value="180">В течение 6 месяцев</option>
                  </Select>
                </div>
              ),
            },
          ]}
          onResetValues={() => {
            form.reset(defaults);
            setParams(toParams(defaults, 1));
          }}
          actions={
            <Button type="submit" disabled={branches.isError} isLoading={query.isFetching}>
              Показать
            </Button>
          }
        />
      </form>

      {(downloadError || branches.isError) && (
        <div
          className="rounded-lg border border-danger/30 bg-danger-subtle p-3 text-sm text-danger-foreground"
          role="alert"
        >
          <p>
            {downloadError ??
              describeApiError(branches.error, "Не удалось загрузить аптечные точки")}
          </p>
          {branches.isError && (
            <Button
              variant="secondary"
              size="sm"
              className="mt-2"
              onClick={() => void branches.refetch()}
            >
              Повторить
            </Button>
          )}
        </div>
      )}

      {query.isLoading ? (
        <SkeletonRows rows={8} />
      ) : query.isError ? (
        <div
          className="rounded-lg border border-danger/30 bg-danger-subtle p-4 text-sm text-danger-foreground"
          role="alert"
        >
          <p>{describeApiError(query.error, "Не удалось загрузить остатки")}</p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-3"
            onClick={() => void query.refetch()}
          >
            Повторить
          </Button>
        </div>
      ) : query.data ? (
        <>
          <div className="grid overflow-hidden rounded-lg border border-border bg-surface sm:grid-cols-3">
            <Summary label="Позиций" value={String(query.data.total)} />
            <Summary label="Упаковок" value={formatQuantity(query.data.total_qty)} />
            <Summary
              label="Закупочная стоимость"
              value={formatMoney(query.data.total_value, query.data.currency)}
            />
          </div>
          {query.data.rows.length === 0 ? (
            <TableEmpty title="Остатков по этим условиям нет">
              Измените дату, аптечную точку или фильтр срока.
            </TableEmpty>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border bg-surface">
              <Table aria-label="Остатки лекарств на выбранную дату">
                <THead>
                  <TR>
                    <TH>Товар</TH>
                    <TH>Партия</TH>
                    <TH>Годен до</TH>
                    <TH className="text-right">Остаток</TH>
                    <TH className="text-right">Закупочная стоимость</TH>
                  </TR>
                </THead>
                <TBody>
                  {query.data.rows.map((row, index) => (
                    <TR
                      key={`${row.branch_name ?? "all"}-${row.batch_number ?? index}-${row.name}`}
                    >
                      <TD>
                        <p className="font-medium text-foreground">{row.name}</p>
                        <p className="mt-0.5 text-xs text-foreground-muted">
                          {[row.inn, row.branch_name].filter(Boolean).join(" · ") ||
                            "Дополнительные сведения не указаны"}
                        </p>
                      </TD>
                      <TD className="font-mono">{row.batch_number || "Не указан"}</TD>
                      <TD>{row.expires_at ? formatDate(row.expires_at) : "Не указан"}</TD>
                      <TD className="text-right font-mono">{formatQuantity(row.qty)}</TD>
                      <TD className="text-right font-mono">
                        {formatMoney(row.value, query.data.currency)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
          <Pagination
            page={query.data.page}
            pageSize={query.data.page_size}
            total={query.data.total}
            onPage={(page) => setParams((current) => ({ ...current, page }))}
          />
        </>
      ) : null}
    </section>
  );
}

function Summary({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="border-b border-border px-4 py-3 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0">
      <p className="text-xs text-foreground-muted">{label}</p>
      <p className="mt-1 font-mono text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}

function toParams(values: FilterValues, page: number): StockOnDateParams {
  return {
    date: values.date,
    branch_id: values.branch_id || undefined,
    query: values.query.trim() || undefined,
    expires_within_days:
      values.expires_within_days === "" ? undefined : Number(values.expires_within_days),
    page,
    page_size: PAGE_SIZE,
  };
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ru-RU").format(new Date(`${value}T00:00:00`));
}

function formatQuantity(value: string): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 }).format(Number(value));
}

function formatMoney(value: string, currency: string): string {
  return `${new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value))} ${currency}`;
}
