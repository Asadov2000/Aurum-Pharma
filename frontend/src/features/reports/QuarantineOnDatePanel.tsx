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
import { calendarDateInTimeZone, DEFAULT_REPORT_TIME_ZONE } from "./calendar";
import {
  getQuarantineReportXlsx,
  quarantineDateSchema,
  quarantineUnitLabels,
  useQuarantineReport,
  type QuarantineReportParams,
} from "./quarantineReport";

const filters = z.object({
  date: quarantineDateSchema,
  branch_id: z.union([z.literal(""), z.string().uuid()]),
});
type Filters = z.infer<typeof filters>;
function parameters(values: Filters, page = 1): QuarantineReportParams {
  return { date: values.date, branch_id: values.branch_id || undefined, page, page_size: 25 };
}
export function QuarantineOnDatePanel({
  reportTimezone = DEFAULT_REPORT_TIME_ZONE,
  canExport = false,
}: {
  reportTimezone?: string;
  canExport?: boolean;
}): JSX.Element {
  const branches = useReportBranches();
  const { user } = useAuth();
  const form = useForm<Filters>({
    defaultValues: {
      date: calendarDateInTimeZone(new Date(), reportTimezone),
      branch_id: branches.defaultBranchId,
    },
  });
  const [params, setParams] = useState(() => parameters(form.getValues()));
  const query = useQuarantineReport(params);
  const values = form.watch();
  const changed =
    values.date !== params.date || (values.branch_id || undefined) !== params.branch_id;
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
      form.setError("date", { message: "Укажите корректную дату и аптечную точку." });
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
      const blob = await getQuarantineReportXlsx(params);
      if (mounted.current && guard() && access === currentAccess.current)
        downloadBlob(blob, `quarantine-${params.date}.xlsx`);
    } catch (cause) {
      if (mounted.current && guard() && access === currentAccess.current)
        setError(describeApiError(cause, "Не удалось скачать отчёт карантина."));
    } finally {
      inFlight.current = false;
      if (mounted.current && guard()) setDownloading(false);
    }
  }
  return (
    <section aria-label="Карантин на дату" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Карантин на дату</h2>
          <p className="text-sm text-foreground-muted">
            Неразрешённые возвраты на конец выбранного дня по часовому поясу аптеки. Эти остатки
            недоступны для продажи.
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
          <Label htmlFor="quarantine-date">Дата карантина</Label>
          <Input id="quarantine-date" type="date" {...form.register("date")} />
        </div>
        <div>
          <Label htmlFor="quarantine-branch">Аптечная точка</Label>
          <Select id="quarantine-branch" {...form.register("branch_id")}>
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
      {(form.formState.errors.date || form.formState.errors.branch_id) && (
        <p role="alert">
          {form.formState.errors.date?.message ?? form.formState.errors.branch_id?.message}
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
          <p>{describeApiError(query.error, "Не удалось загрузить карантин.")}</p>
          <Button onClick={() => void query.refetch()}>Повторить</Button>
        </div>
      ) : query.data ? (
        <>
          <div
            aria-label="Итоги карантина"
            role="region"
            className="rounded-lg border p-4 space-y-1"
          >
            <p>Позиций: {query.data.total}</p>
            <p>Обычные упаковки: {quantity(query.data.total_qty)} уп.</p>
            {query.data.physical_quantities.map((item) => (
              <p key={item.unit}>
                Физические единицы: {BigInt(item.quantity).toLocaleString("ru-RU")}{" "}
                {quarantineUnitLabels[item.unit]}
              </p>
            ))}
            <p>
              {query.data.cost_status === "partial"
                ? "Известная закупочная стоимость"
                : "Закупочная стоимость"}
              :{" "}
              {cost(
                query.data.cost_status === "partial"
                  ? query.data.known_total_value
                  : query.data.total_value,
                query.data.cost_status,
              )}
            </p>
            {query.data.cost_status === "partial" && (
              <p>Стоимость части возвратов недоступна или неизвестна.</p>
            )}
          </div>
          {query.data.rows.length === 0 ? (
            <TableEmpty title="Неразрешённых возвратов на эту дату нет">
              Измените дату или аптечную точку.
            </TableEmpty>
          ) : (
            <Table aria-label="Неразрешённые возвраты на выбранную дату">
              <THead>
                <TR>
                  <TH>Товар</TH>
                  <TH>Аптечная точка</TH>
                  <TH>Принят в карантин</TH>
                  <TH>Количество</TH>
                  <TH>Закупочная стоимость</TH>
                </TR>
              </THead>
              <TBody>
                {query.data.rows.map((row) => (
                  <TR
                    key={`${row.source_kind}-${row.intake_operation_id}-${row.line_id}-${row.stock_id}`}
                  >
                    <TD>{row.name}</TD>
                    <TD>{row.branch_name}</TD>
                    <TD>
                      {new Intl.DateTimeFormat("ru-RU", {
                        timeZone: reportTimezone,
                        dateStyle: "short",
                        timeStyle: "short",
                      }).format(new Date(row.received_at))}
                    </TD>
                    <TD>
                      {row.physical_quantity
                        ? `${BigInt(row.physical_quantity.quantity).toLocaleString("ru-RU")} ${quarantineUnitLabels[row.physical_quantity.unit]}`
                        : `${quantity(row.qty)} уп.`}
                    </TD>
                    <TD>{cost(row.value, row.cost_status)}</TD>
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
function quantity(value: string) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 }).format(Number(value));
}
function cost(value: string | null, status: string) {
  if (status === "hidden") return "Скрыто";
  if (value === null) return "Нет данных о стоимости";
  return `${new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value))} TJS`;
}
