import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfigurableFilterBar,
  Input,
  Label,
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
import { useBranchesQuery, useRegistersQuery } from "@/features/foundation/queries";
import { describeApiError } from "@/lib/errorMessages";

import { recentReportRange } from "./calendar";
import { useShiftHistoryQuery } from "./queries";
import { type ShiftHistoryItem, type ShiftHistoryParams } from "./types";

const PAGE_SIZE = 25;
const LAST_CLOSED_KEY = "pos:lastClosedShiftId";

const filterSchema = z
  .object({
    date_from: z.string(),
    date_to: z.string(),
    branch_id: z.string(),
    register_id: z.string(),
    cashier_query: z.string().trim().max(100, "Не более 100 символов"),
  })
  .refine(({ date_from, date_to }) => !date_from || !date_to || date_from <= date_to, {
    message: "Начало периода позже конца",
    path: ["date_to"],
  });

type FilterValues = z.infer<typeof filterSchema>;

interface ShiftHistoryPanelProps {
  selectedShift: ShiftHistoryItem | null;
  onSelect: (shift: ShiftHistoryItem | null) => void;
  reportTimezone: string;
}

function defaultFilters(reportTimezone: string): FilterValues {
  const range = recentReportRange(reportTimezone);
  return {
    date_from: range.dateFrom,
    date_to: range.dateTo,
    branch_id: "",
    register_id: "",
    cashier_query: "",
  };
}

function toParams(values: FilterValues, page: number): ShiftHistoryParams {
  return {
    status: "closed",
    branch_id: values.branch_id || undefined,
    register_id: values.register_id || undefined,
    cashier_query: values.cashier_query || undefined,
    date_from: values.date_from || undefined,
    date_to: values.date_to || undefined,
    page,
    page_size: PAGE_SIZE,
  };
}

export function ShiftHistoryPanel({
  selectedShift,
  onSelect,
  reportTimezone,
}: ShiftHistoryPanelProps): JSX.Element {
  const filterPreferenceKey = useFilterPreferenceKey("reports-shift-history");
  const defaults = useMemo(() => defaultFilters(reportTimezone), [reportTimezone]);
  const form = useForm<FilterValues>({ defaultValues: defaults });
  const [filters, setFilters] = useState<FilterValues>(defaults);
  const [page, setPage] = useState(1);
  const watchedFilters = form.watch();
  const branchId = form.watch("branch_id");
  const branches = useBranchesQuery(false);
  const registers = useRegistersQuery(branchId || null, false);
  const history = useShiftHistoryQuery(toParams(filters, page));
  const lastClosedShiftId = useMemo(() => window.localStorage.getItem(LAST_CLOSED_KEY), []);

  useEffect(() => {
    if (selectedShift || !lastClosedShiftId || !history.data) return;
    const recent = history.data.items.find((shift) => shift.id === lastClosedShiftId);
    if (recent) onSelect(recent);
  }, [history.data, lastClosedShiftId, onSelect, selectedShift]);

  const applyFilters = form.handleSubmit((values) => {
    const parsed = filterSchema.safeParse(values);
    if (!parsed.success) {
      const issue = parsed.error.issues[0];
      form.setError("date_to", {
        type: "validate",
        message: issue?.message ?? "Проверьте фильтры",
      });
      return;
    }
    setFilters(parsed.data);
    setPage(1);
    onSelect(null);
  });

  const resetFilters = () => {
    const next = defaultFilters(reportTimezone);
    form.reset(next);
    setFilters(next);
    setPage(1);
    onSelect(null);
  };

  const filterOptionsError = branches.error ?? registers.error;
  const total = history.data?.total ?? 0;

  return (
    <section className="space-y-3" aria-labelledby="shift-history-title">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle id="shift-history-title">Z-отчёты по сменам</CardTitle>
            <span className="text-sm text-foreground-muted">найдено: {total}</span>
          </div>
        </CardHeader>
        <CardContent>
          <form onSubmit={(event) => void applyFilters(event)}>
            <ConfigurableFilterBar
              preferenceKey={filterPreferenceKey}
              filters={[
                {
                  id: "period",
                  label: "Период",
                  content: (
                    <div className="grid w-64 grid-cols-1 gap-2 sm:w-auto sm:grid-cols-2">
                      <div>
                        <Label htmlFor="shift_date_from">Открыта с</Label>
                        <Input id="shift_date_from" type="date" {...form.register("date_from")} />
                      </div>
                      <div>
                        <Label htmlFor="shift_date_to">По</Label>
                        <Input id="shift_date_to" type="date" {...form.register("date_to")} />
                        {form.formState.errors.date_to && (
                          <p className="mt-1 text-xs text-danger">
                            {form.formState.errors.date_to.message}
                          </p>
                        )}
                      </div>
                    </div>
                  ),
                  active:
                    watchedFilters.date_from !== defaults.date_from ||
                    watchedFilters.date_to !== defaults.date_to,
                  onClear: () => {
                    form.setValue("date_from", defaults.date_from);
                    form.setValue("date_to", defaults.date_to);
                    setFilters((current) => ({
                      ...current,
                      date_from: defaults.date_from,
                      date_to: defaults.date_to,
                    }));
                    setPage(1);
                    onSelect(null);
                  },
                  defaultVisible: true,
                },
                {
                  id: "branch",
                  label: "Точка",
                  content: (
                    <div>
                      <Label htmlFor="shift_branch">Точка</Label>
                      <Select
                        id="shift_branch"
                        className="w-44"
                        {...form.register("branch_id", {
                          onChange: () => form.setValue("register_id", ""),
                        })}
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
                  active: Boolean(watchedFilters.branch_id),
                  onClear: () => {
                    form.setValue("branch_id", "");
                    form.setValue("register_id", "");
                    setFilters((current) => ({
                      ...current,
                      branch_id: "",
                      register_id: "",
                    }));
                    setPage(1);
                    onSelect(null);
                  },
                  defaultVisible: true,
                },
                {
                  id: "register",
                  label: "Касса",
                  content: (
                    <div>
                      <Label htmlFor="shift_register">Касса</Label>
                      <Select
                        id="shift_register"
                        className="w-44"
                        {...form.register("register_id")}
                      >
                        <option value="">Все доступные</option>
                        {registers.data?.map((register) => (
                          <option key={register.id} value={register.id}>
                            {register.name}
                          </option>
                        ))}
                      </Select>
                    </div>
                  ),
                  active: Boolean(watchedFilters.register_id),
                  onClear: () => {
                    form.setValue("register_id", "");
                    setFilters((current) => ({ ...current, register_id: "" }));
                    setPage(1);
                    onSelect(null);
                  },
                },
                {
                  id: "cashier",
                  label: "Кассир",
                  content: (
                    <div>
                      <Label htmlFor="shift_cashier">Кассир</Label>
                      <Input
                        id="shift_cashier"
                        className="w-44"
                        placeholder="Имя сотрудника"
                        {...form.register("cashier_query")}
                      />
                    </div>
                  ),
                  active: Boolean(watchedFilters.cashier_query),
                  onClear: () => {
                    form.setValue("cashier_query", "");
                    setFilters((current) => ({ ...current, cashier_query: "" }));
                    setPage(1);
                    onSelect(null);
                  },
                  defaultVisible: true,
                },
              ]}
              onResetValues={resetFilters}
              actions={<Button type="submit">Показать</Button>}
            />
          </form>
          {filterOptionsError && (
            <p className="mt-2 text-xs text-danger">
              {describeApiError(filterOptionsError, "Не удалось загрузить точки и кассы")}
            </p>
          )}
        </CardContent>
      </Card>

      {history.error && (
        <div className="flex flex-wrap items-center gap-3 text-sm text-danger">
          <span>{describeApiError(history.error, "Не удалось загрузить смены")}</span>
          <Button variant="secondary" size="sm" onClick={() => void history.refetch()}>
            Повторить
          </Button>
        </div>
      )}

      {history.isLoading ? (
        <SkeletonRows rows={5} />
      ) : !history.data || history.data.items.length === 0 ? (
        <TableEmpty title="Закрытых смен не найдено">Измените период или фильтры.</TableEmpty>
      ) : (
        <>
          <Table>
            <THead>
              <TR>
                <TH>Смена</TH>
                <TH>Точка / касса</TH>
                <TH>Кассир</TH>
                <TH className="text-right">Продажи / возвраты</TH>
                <TH>Чеки</TH>
                <TH className="text-right">Расхождение</TH>
                <TH className="w-24">
                  <span className="sr-only">Действия</span>
                </TH>
              </TR>
            </THead>
            <TBody>
              {history.data.items.map((shift) => {
                const difference = Number(shift.closing_difference ?? 0);
                return (
                  <TR
                    key={shift.id}
                    aria-selected={selectedShift?.id === shift.id}
                    className={selectedShift?.id === shift.id ? "bg-primary/5" : undefined}
                  >
                    <TD>
                      <p className="font-medium">
                        {formatDateTime(shift.opened_at, reportTimezone)}
                      </p>
                      <p className="text-xs text-foreground-muted">
                        до {formatDateTime(shift.closed_at, reportTimezone)}
                      </p>
                    </TD>
                    <TD>
                      <p>{shift.branch_name}</p>
                      <p className="text-xs text-foreground-muted">{shift.register_name}</p>
                    </TD>
                    <TD>{shift.cashier_name ?? "Не указан"}</TD>
                    <TD className="text-right">
                      <p className="font-mono">{formatMoney(shift.sales_total, shift.currency)}</p>
                      <p className="text-xs text-foreground-muted">
                        возвраты: {formatMoney(shift.returns_total, shift.currency)}
                      </p>
                    </TD>
                    <TD>
                      {shift.sales_count} / {shift.returns_count}
                    </TD>
                    <TD className="text-right">
                      <Badge
                        tone={difference === 0 ? "success" : difference < 0 ? "danger" : "info"}
                      >
                        {formatMoney(shift.closing_difference ?? "0", shift.currency)}
                      </Badge>
                    </TD>
                    <TD className="text-right">
                      <Button
                        size="sm"
                        variant={selectedShift?.id === shift.id ? "primary" : "secondary"}
                        onClick={() => onSelect(shift)}
                      >
                        {selectedShift?.id === shift.id ? "Открыт" : "Открыть"}
                      </Button>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={history.data.total}
            onPage={setPage}
          />
        </>
      )}
    </section>
  );
}

function formatDateTime(value: string | null, reportTimezone: string): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU", {
    timeZone: reportTimezone,
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatMoney(value: string, currency: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return `0,00 ${currency}`;
  return `${amount.toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`;
}
