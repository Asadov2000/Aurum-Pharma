import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import {
  Button,
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
import { useAuth } from "@/features/auth/hooks";
import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import { describeApiError } from "@/features/foundation/errors";
import { downloadBlob } from "@/lib/download";
import { useReportBranches } from "./branchScope";
import { currentReportMonthRange, DEFAULT_REPORT_TIME_ZONE } from "./calendar";
import { quarantineDateSchema } from "./quarantineReport";
import {
  getGrossProfitReportXlsx,
  grossProfitPeriodSchema,
  useGrossProfitReport,
  type GrossProfitReportParams,
} from "./grossProfitReport";
const filters = z
  .object({
    date_from: quarantineDateSchema,
    date_to: quarantineDateSchema,
    branch_id: z.union([z.literal(""), z.string().uuid()]),
  })
  .superRefine((values, ctx) => {
    const period = grossProfitPeriodSchema.safeParse(values);
    if (!period.success) for (const issue of period.error.issues) ctx.addIssue(issue);
  });
type Filters = z.infer<typeof filters>;
function parameters(values: Filters, page = 1): GrossProfitReportParams {
  return {
    date_from: values.date_from,
    date_to: values.date_to,
    branch_id: values.branch_id || undefined,
    page,
    page_size: 25,
  };
}
const eventLabels = {
  sale: "Продажа",
  refund: "Возврат денег",
  zero_return: "Возврат без выплаты",
  writeoff: "Списание",
};
export function GrossProfitPanel({
  reportTimezone = DEFAULT_REPORT_TIME_ZONE,
  canExport = false,
}: {
  reportTimezone?: string;
  canExport?: boolean;
}): JSX.Element {
  const branches = useReportBranches();
  const { user } = useAuth();
  const range = currentReportMonthRange(reportTimezone);
  const form = useForm<Filters>({
    defaultValues: {
      date_from: range.dateFrom,
      date_to: range.dateTo,
      branch_id: branches.defaultBranchId,
    },
  });
  const [params, setParams] = useState(() => parameters(form.getValues()));
  const query = useGrossProfitReport(params);
  const values = form.watch();
  const changed =
    values.date_from !== params.date_from ||
    values.date_to !== params.date_to ||
    (values.branch_id || undefined) !== params.branch_id;
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const currentAccess = useRef("");
  currentAccess.current = JSON.stringify([
    user?.id,
    user?.active_tenant_id,
    user?.permissions,
    user?.permission_scopes,
    user?.support_access,
    canExport,
  ]);
  const apply = form.handleSubmit((raw) => {
    const checked = filters.safeParse(raw);
    if (!checked.success) {
      form.setError("date_from", {
        message: checked.error.issues[0]?.message ?? "Укажите корректный период.",
      });
      return;
    }
    if (branches.scope !== null && !branches.scope.includes(checked.data.branch_id)) {
      form.setError("branch_id", { message: "Выберите доступную аптечную точку." });
      return;
    }
    form.clearErrors();
    const next = parameters(checked.data);
    if (JSON.stringify(next) === JSON.stringify(params)) void query.refetch();
    setParams(next);
  });
  async function download() {
    if (
      inFlight.current ||
      !canExport ||
      changed ||
      !query.data ||
      query.isFetching ||
      branches.isError ||
      !user?.active_tenant_id
    )
      return;
    const guard = capturePosSessionGuard({ tenantId: user.active_tenant_id, userId: user.id });
    const access = currentAccess.current;
    inFlight.current = true;
    setDownloading(true);
    setError(null);
    try {
      const blob = await getGrossProfitReportXlsx(params);
      if (mounted.current && guard() && access === currentAccess.current)
        downloadBlob(blob, `gross-profit-${params.date_from}-${params.date_to}.xlsx`);
    } catch (cause) {
      if (mounted.current && guard() && access === currentAccess.current)
        setError(describeApiError(cause, "Не удалось скачать отчёт валовой прибыли."));
    } finally {
      inFlight.current = false;
      if (mounted.current && guard()) setDownloading(false);
    }
  }
  return (
    <section aria-label="Валовая прибыль" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Валовая прибыль</h2>
          <p className="text-sm text-foreground-muted">
            Выручка за вычетом возвратов и себестоимости проданных товаров. Списания показаны
            отдельно. Это не чистая прибыль: аренда, зарплаты и другие расходы здесь не учтены.
          </p>
        </div>
        {canExport && (
          <Button
            variant="secondary"
            disabled={changed || !query.data || query.isFetching || branches.isError}
            isLoading={downloading}
            onClick={() => void download()}
          >
            Скачать в Excel
          </Button>
        )}
      </div>
      <form onSubmit={(event) => void apply(event)} className="flex flex-wrap items-end gap-3">
        <div>
          <Label htmlFor="profit-from">Начало периода</Label>
          <Input id="profit-from" type="date" {...form.register("date_from")} />
        </div>
        <div>
          <Label htmlFor="profit-to">Конец периода</Label>
          <Input id="profit-to" type="date" {...form.register("date_to")} />
        </div>
        <div>
          <Label htmlFor="profit-branch">Аптечная точка</Label>
          <Select id="profit-branch" {...form.register("branch_id")}>
            {branches.scope === null && <option value="">Все доступные точки</option>}
            {branches.data?.map((branch) => (
              <option key={branch.id} value={branch.id}>
                {branch.name}
              </option>
            ))}
          </Select>
        </div>
        <Button
          type="submit"
          disabled={branches.isError || !branches.canView}
          isLoading={query.isFetching}
        >
          Показать
        </Button>
      </form>
      {(form.formState.errors.date_from || form.formState.errors.branch_id) && (
        <p role="alert">
          {form.formState.errors.date_from?.message ?? form.formState.errors.branch_id?.message}
        </p>
      )}
      {changed && <p>Условия изменены. Нажмите «Показать», чтобы обновить отчёт.</p>}
      {(error || branches.isError) && (
        <p role="alert">{error ?? "Не удалось загрузить аптечные точки."}</p>
      )}
      {query.isLoading ? (
        <SkeletonRows rows={5} />
      ) : query.isError ? (
        <div role="alert">
          <p>{describeApiError(query.error, "Не удалось загрузить валовую прибыль.")}</p>
          <Button onClick={() => void query.refetch()}>Повторить</Button>
        </div>
      ) : query.data ? (
        <>
          <div
            role="region"
            aria-label="Итоги валовой прибыли"
            className="rounded-lg border p-4 space-y-1"
          >
            <p>Выручка продаж: {money(query.data.sales_revenue)}</p>
            <p>Возвраты: {money(query.data.refunds)}</p>
            <p>Выручка за вычетом возвратов: {money(query.data.net_revenue)}</p>
            <p>
              Себестоимость продаж за вычетом возвратов:{" "}
              {cost(query.data.net_sales_cost, query.data.cost_status)}
            </p>
            <p>Валовая прибыль: {cost(query.data.gross_profit, query.data.cost_status)}</p>
            {query.data.cost_status === "partial" && (
              <div>
                <p>Полный итог неизвестен: стоимость части операций недоступна или неизвестна.</p>
                <p>
                  По операциям с известной стоимостью — себестоимость:{" "}
                  {money(query.data.known_net_sales_cost)}, валовая прибыль:{" "}
                  {money(query.data.known_gross_profit)}.
                </p>
              </div>
            )}
          </div>
          <div role="region" aria-label="Стоимость списаний" className="rounded-lg border p-4">
            <p>
              Стоимость списаний: {cost(query.data.writeoff_cost, query.data.writeoff_cost_status)}
            </p>
            {query.data.writeoff_cost_status === "partial" && (
              <p>
                Известная стоимость списаний: {money(query.data.known_writeoff_cost)}. Стоимость
                части списаний недоступна или неизвестна.
              </p>
            )}
          </div>
          {query.data.rows.length === 0 ? (
            <TableEmpty title="Операций за этот период нет">
              Измените период или аптечную точку.
            </TableEmpty>
          ) : (
            <Table aria-label="Операции валовой прибыли">
              <THead>
                <TR>
                  <TH>Дата</TH>
                  <TH>Товар</TH>
                  <TH>Аптечная точка</TH>
                  <TH>Операция</TH>
                  <TH>Выручка</TH>
                  <TH>Себестоимость</TH>
                  <TH>Валовая прибыль</TH>
                </TR>
              </THead>
              <TBody>
                {query.data.rows.map((row) => (
                  <TR
                    key={`${row.source_kind}-${row.event_kind}-${row.event_id}-${row.line_id ?? "none"}`}
                  >
                    <TD>
                      {new Intl.DateTimeFormat("ru-RU", {
                        timeZone: reportTimezone,
                        dateStyle: "short",
                        timeStyle: "short",
                      }).format(new Date(row.occurred_at))}
                    </TD>
                    <TD>{row.name}</TD>
                    <TD>{row.branch_name}</TD>
                    <TD>{eventLabels[row.event_kind]}</TD>
                    <TD>{money(row.revenue)}</TD>
                    <TD>{cost(row.cost, row.cost_status)}</TD>
                    <TD>
                      {row.event_kind === "writeoff"
                        ? "Учитывается отдельно"
                        : cost(row.gross_profit, row.cost_status)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          <Pagination
            page={query.data.page}
            pageSize={query.data.page_size}
            total={query.data.total}
            onPage={(page) => setParams((previous) => ({ ...previous, page }))}
          />
        </>
      ) : null}
    </section>
  );
}
function money(value: string | null): string {
  return value === null
    ? "Нет данных о стоимости"
    : `${new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value))} TJS`;
}
function cost(value: string | null, status: string): string {
  return status === "hidden" ? "Скрыто" : money(value);
}
