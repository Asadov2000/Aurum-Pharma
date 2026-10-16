import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { useAuth } from "@/features/auth/hooks";
import { capturePosSessionGuard, currentPosStorageScope } from "@/features/pos/posStorageScope";
import { api, withAuthSession } from "@/lib/api";
import { useReportBranchScope } from "./branchScope";
import { quarantineDateSchema } from "./quarantineReport";

const decimal = z.string().regex(/^-?\d+(?:\.\d+)?$/);
const status = z.enum(["known", "partial", "hidden"]);
const rowSchema = z
  .object({
    event_id: z.string().uuid(),
    line_id: z.string().uuid().nullable(),
    branch_id: z.string().uuid(),
    branch_name: z.string(),
    name: z.string(),
    occurred_at: z.string().datetime({ offset: true }),
    source_kind: z.enum(["legacy", "physical", "mixed"]),
    event_kind: z.enum(["sale", "refund", "zero_return", "writeoff"]),
    revenue: decimal,
    cost: decimal.nullable(),
    gross_profit: decimal.nullable(),
    cost_status: z.enum(["known", "unknown", "hidden"]),
  })
  .strict()
  .superRefine((row, ctx) => {
    if (
      (row.cost_status === "known") !== (row.cost !== null) ||
      (row.event_kind === "writeoff"
        ? row.gross_profit !== null || Number(row.revenue) !== 0
        : (row.cost_status === "known") !== (row.gross_profit !== null))
    )
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Некорректная стоимость операции" });
  });
export const grossProfitReportSchema = z
  .object({
    date_from: quarantineDateSchema,
    date_to: quarantineDateSchema,
    currency: z.literal("TJS"),
    rows: z.array(rowSchema),
    total: z.number().int().nonnegative(),
    page: z.number().int().positive(),
    page_size: z.number().int().min(1).max(100),
    sales_revenue: decimal,
    refunds: decimal,
    net_revenue: decimal,
    net_sales_cost: decimal.nullable(),
    known_net_sales_cost: decimal.nullable(),
    gross_profit: decimal.nullable(),
    known_gross_profit: decimal.nullable(),
    writeoff_cost: decimal.nullable(),
    known_writeoff_cost: decimal.nullable(),
    cost_status: status,
    writeoff_cost_status: status,
  })
  .strict()
  .superRefine((data, ctx) => {
    const invalid = (state: z.infer<typeof status>, full: string | null, known: string | null) =>
      (state === "known") !== (full !== null) || (state === "hidden") !== (known === null);
    if (
      invalid(data.cost_status, data.net_sales_cost, data.known_net_sales_cost) ||
      invalid(data.cost_status, data.gross_profit, data.known_gross_profit) ||
      invalid(data.writeoff_cost_status, data.writeoff_cost, data.known_writeoff_cost)
    )
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Некорректный итог валовой прибыли" });
  });
export const grossProfitPeriodSchema = z
  .object({ date_from: quarantineDateSchema, date_to: quarantineDateSchema })
  .refine((value) => value.date_from <= value.date_to, "Начало периода должно быть не позже конца")
  .refine(
    (value) =>
      (Date.parse(`${value.date_to}T00:00:00Z`) - Date.parse(`${value.date_from}T00:00:00Z`)) /
        86400000 <
      366,
    "Выберите период не более 366 дней включительно",
  );
const paramsSchema = z
  .object({
    date_from: quarantineDateSchema,
    date_to: quarantineDateSchema,
    branch_id: z.string().uuid().optional(),
    page: z.number().int().positive(),
    page_size: z.number().int().min(1).max(100),
  })
  .strict()
  .refine(
    (value) => grossProfitPeriodSchema.safeParse(value).success,
    "Укажите корректный период не более 366 дней",
  );
export type GrossProfitReport = z.infer<typeof grossProfitReportSchema>;
export type GrossProfitReportParams = z.infer<typeof paramsSchema>;
function sessionGuard() {
  const scope = currentPosStorageScope();
  if (!scope) throw new Error("Войдите в аккаунт сотрудника.");
  const active = capturePosSessionGuard(scope);
  return () => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте отчёт заново.");
  };
}
export async function getGrossProfitReport(
  params: GrossProfitReportParams,
  signal?: AbortSignal,
): Promise<GrossProfitReport> {
  const checked = paramsSchema.parse(params);
  const guard = sessionGuard();
  const { data } = await api.get<unknown>(
    "/reports/gross-profit",
    withAuthSession({ params: checked, signal }, true),
  );
  guard();
  const report = grossProfitReportSchema.parse(data);
  if (
    report.date_from !== checked.date_from ||
    report.date_to !== checked.date_to ||
    report.page !== checked.page ||
    report.page_size !== checked.page_size ||
    (checked.branch_id && report.rows.some((row) => row.branch_id !== checked.branch_id))
  )
    throw new Error("Ответ относится к другим условиям отчёта.");
  return report;
}
export async function getGrossProfitReportXlsx(params: GrossProfitReportParams): Promise<Blob> {
  const checked = paramsSchema.parse(params);
  const guard = sessionGuard();
  const { data } = await api.get<Blob>(
    "/reports/gross-profit.xlsx",
    withAuthSession(
      {
        params: {
          date_from: checked.date_from,
          date_to: checked.date_to,
          branch_id: checked.branch_id,
        },
        responseType: "blob",
      },
      true,
    ),
  );
  guard();
  return data;
}
export function useGrossProfitReport(params: GrossProfitReportParams) {
  const { user } = useAuth();
  const { canView, scope, scopeKey } = useReportBranchScope();
  const allowed =
    canView &&
    (params.branch_id ? scope === null || scope.includes(params.branch_id) : scope === null);
  return useQuery({
    queryKey: [
      "reports",
      "gross-profit",
      user?.id,
      user?.active_tenant_id,
      user?.permissions,
      user?.permission_scopes,
      user?.support_access,
      scopeKey,
      params,
    ],
    queryFn: ({ signal }) => getGrossProfitReport(params, signal),
    enabled: allowed,
  });
}
