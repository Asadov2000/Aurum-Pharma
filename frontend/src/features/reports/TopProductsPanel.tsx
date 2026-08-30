import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  Input,
  Label,
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
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";

import { currentReportMonthRange } from "./calendar";
import { useTopProductsQuery } from "./queries";
import { type TopProductsParams } from "./types";

const filterSchema = z
  .object({
    from: z.string().min(1, "Укажите начало периода"),
    to: z.string().min(1, "Укажите конец периода"),
    branch_id: z.string(),
    sort_by: z.enum(["revenue", "quantity"]),
    limit: z.coerce.number().int().min(5).max(100),
  })
  .refine(({ from, to }) => from <= to, {
    message: "Начало периода позже конца",
    path: ["to"],
  });

type FilterValues = z.infer<typeof filterSchema>;

export function TopProductsPanel({ reportTimezone }: { reportTimezone: string }): JSX.Element {
  const range = useMemo(() => currentReportMonthRange(reportTimezone), [reportTimezone]);
  const defaults = useMemo<FilterValues>(
    () => ({
      from: range.dateFrom,
      to: range.dateTo,
      branch_id: "",
      sort_by: "revenue",
      limit: 20,
    }),
    [range],
  );
  const form = useForm<FilterValues>({ defaultValues: defaults });
  const [params, setParams] = useState<TopProductsParams>(() => toParams(defaults));
  const branches = useBranchesQuery(false);
  const query = useTopProductsQuery(params);

  const apply = form.handleSubmit((values) => {
    const parsed = filterSchema.safeParse(values);
    if (!parsed.success) {
      form.setError("to", {
        type: "validate",
        message: parsed.error.issues[0]?.message ?? "Проверьте фильтры",
      });
      return;
    }
    form.clearErrors();
    setParams(toParams(parsed.data));
  });

  return (
    <section className="space-y-3" aria-labelledby="top-products-title">
      <div>
        <h2 id="top-products-title" className="text-lg font-semibold text-foreground">
          Товары-лидеры
        </h2>
        <p className="mt-0.5 text-sm text-foreground-muted">
          Показывает, что приносит больше выручки или продаётся чаще. Возвраты уже вычтены.
        </p>
      </div>

      <form
        className="grid gap-3 rounded-lg border border-border bg-surface p-4 sm:grid-cols-2 xl:grid-cols-[1fr_1fr_1.4fr_1fr_0.8fr_auto] xl:items-end"
        onSubmit={(event) => void apply(event)}
      >
        <div>
          <Label htmlFor="top_products_from">С</Label>
          <Input id="top_products_from" type="date" {...form.register("from")} />
        </div>
        <div>
          <Label htmlFor="top_products_to">По</Label>
          <Input id="top_products_to" type="date" {...form.register("to")} />
          {form.formState.errors.to && (
            <p className="mt-1 text-xs text-danger">{form.formState.errors.to.message}</p>
          )}
        </div>
        <div>
          <Label htmlFor="top_products_branch">Аптечная точка</Label>
          <Select id="top_products_branch" {...form.register("branch_id")}>
            <option value="">Все доступные точки</option>
            {branches.data?.map((branch) => (
              <option key={branch.id} value={branch.id}>
                {branch.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="top_products_sort">Рейтинг по</Label>
          <Select id="top_products_sort" {...form.register("sort_by")}>
            <option value="revenue">Выручке</option>
            <option value="quantity">Количеству</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="top_products_limit">Показать</Label>
          <Select id="top_products_limit" {...form.register("limit", { valueAsNumber: true })}>
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </Select>
        </div>
        <Button type="submit" disabled={branches.isError} isLoading={query.isFetching}>
          Показать
        </Button>
      </form>

      {branches.isError && (
        <ErrorState
          message={describeApiError(branches.error, "Не удалось загрузить аптечные точки")}
          onRetry={() => void branches.refetch()}
        />
      )}
      {query.isLoading ? (
        <SkeletonRows rows={8} />
      ) : query.isError ? (
        <ErrorState
          message={describeApiError(query.error, "Не удалось построить рейтинг товаров")}
          onRetry={() => void query.refetch()}
        />
      ) : query.data?.rows.length ? (
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <Table aria-label="Товары-лидеры за выбранный период">
            <THead>
              <TR>
                <TH className="w-14">Место</TH>
                <TH>Товар</TH>
                <TH className="text-right">Продано</TH>
                <TH className="text-right">Чеков</TH>
                <TH className="text-right">Выручка</TH>
              </TR>
            </THead>
            <TBody>
              {query.data.rows.map((row, index) => (
                <TR key={row.catalog_id}>
                  <TD className="font-mono text-foreground-muted">{index + 1}</TD>
                  <TD>
                    <p className="font-medium text-foreground">{row.name}</p>
                    <p className="mt-0.5 text-xs text-foreground-muted">
                      {[row.form, row.dosage, row.pack_size].filter(Boolean).join(" · ") ||
                        "Форма выпуска не указана"}
                    </p>
                  </TD>
                  <TD className="text-right font-mono">{formatQuantity(row.quantity)}</TD>
                  <TD className="text-right font-mono">{row.receipts_count}</TD>
                  <TD className="text-right font-mono font-semibold">
                    {formatMoney(row.revenue, query.data.currency)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      ) : (
        <TableEmpty title="Продаж за выбранный период нет">
          Измените даты или аптечную точку.
        </TableEmpty>
      )}
    </section>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }): JSX.Element {
  return (
    <div
      className="rounded-lg border border-danger/30 bg-danger-subtle p-4 text-sm text-danger-foreground"
      role="alert"
    >
      <p>{message}</p>
      <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
        Повторить
      </Button>
    </div>
  );
}

function toParams(values: FilterValues): TopProductsParams {
  return {
    from: values.from,
    to: values.to,
    branch_id: values.branch_id || undefined,
    sort_by: values.sort_by,
    limit: values.limit,
  };
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
