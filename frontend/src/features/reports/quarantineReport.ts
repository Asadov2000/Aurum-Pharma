import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { useAuth } from "@/features/auth/hooks";
import { capturePosSessionGuard, currentPosStorageScope } from "@/features/pos/posStorageScope";
import { api, withAuthSession } from "@/lib/api";
import { useReportBranchScope } from "./branchScope";

export const quarantineDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .refine((value) => {
    const date = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
  }, "Укажите корректную дату");
const decimal = z.string().regex(/^\d+(?:\.\d+)?$/);
const integer = z.string().regex(/^(0|[1-9]\d*)$/);
const quantity = z
  .object({
    unit: z.enum(["tablet", "capsule", "ampoule", "sachet", "suppository"]),
    quantity: integer,
  })
  .strict();
const rowSchema = z
  .object({
    inventory_kind: z.enum(["legacy", "physical"]),
    source_kind: z.enum(["legacy", "physical", "mixed"]),
    intake_operation_id: z.string().uuid(),
    line_id: z.string().uuid(),
    stock_id: z.string().uuid(),
    branch_id: z.string().uuid(),
    branch_name: z.string(),
    name: z.string(),
    received_at: z.string().datetime({ offset: true }),
    qty: decimal,
    physical_quantity: quantity.nullable(),
    value: decimal.nullable(),
    cost_status: z.enum(["known", "unknown", "hidden"]),
  })
  .strict()
  .superRefine((row, ctx) => {
    if (
      (row.cost_status === "known") !== (row.value !== null) ||
      (row.inventory_kind === "physical"
        ? row.physical_quantity === null || Number(row.qty) !== 0
        : row.physical_quantity !== null)
    )
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Некорректные единицы или стоимость карантина",
      });
  });
export const quarantineReportSchema = z
  .object({
    on_date: quarantineDateSchema,
    currency: z.literal("TJS"),
    rows: z.array(rowSchema),
    total: z.number().int().nonnegative(),
    page: z.number().int().positive(),
    page_size: z.number().int().min(1).max(100),
    total_qty: decimal,
    physical_quantities: z.array(quantity),
    total_value: decimal.nullable(),
    known_total_value: decimal.nullable(),
    cost_status: z.enum(["known", "partial", "hidden"]),
  })
  .strict()
  .superRefine((data, ctx) => {
    if (
      (data.cost_status === "known") !== (data.total_value !== null) ||
      (data.cost_status === "hidden") !== (data.known_total_value === null)
    )
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Некорректный итог стоимости карантина",
      });
  });
const paramsSchema = z
  .object({
    date: quarantineDateSchema,
    branch_id: z.string().uuid().optional(),
    page: z.number().int().positive(),
    page_size: z.number().int().min(1).max(100),
  })
  .strict();
export type QuarantineReport = z.infer<typeof quarantineReportSchema>;
export type QuarantineReportParams = z.infer<typeof paramsSchema>;
export const quarantineUnitLabels: Record<z.infer<typeof quantity>["unit"], string> = {
  tablet: "табл.",
  capsule: "капс.",
  ampoule: "амп.",
  sachet: "саше",
  suppository: "супп.",
};
function sessionGuard() {
  const scope = currentPosStorageScope();
  if (!scope) throw new Error("Войдите в аккаунт сотрудника.");
  const active = capturePosSessionGuard(scope);
  return () => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте отчёт заново.");
  };
}
export async function getQuarantineReport(
  params: QuarantineReportParams,
  signal?: AbortSignal,
): Promise<QuarantineReport> {
  const checked = paramsSchema.parse(params);
  const guard = sessionGuard();
  const { data } = await api.get<unknown>(
    "/reports/quarantine-on-date",
    withAuthSession({ params: checked, signal }, true),
  );
  guard();
  const report = quarantineReportSchema.parse(data);
  if (
    report.on_date !== checked.date ||
    report.page !== checked.page ||
    report.page_size !== checked.page_size ||
    (checked.branch_id && report.rows.some((row) => row.branch_id !== checked.branch_id))
  )
    throw new Error("Ответ относится к другим условиям отчёта.");
  return report;
}
export async function getQuarantineReportXlsx(params: QuarantineReportParams): Promise<Blob> {
  const checked = paramsSchema.parse(params);
  const guard = sessionGuard();
  const { data } = await api.get<Blob>(
    "/reports/quarantine-on-date.xlsx",
    withAuthSession(
      { params: { date: checked.date, branch_id: checked.branch_id }, responseType: "blob" },
      true,
    ),
  );
  guard();
  return data;
}
export function useQuarantineReport(params: QuarantineReportParams) {
  const { user } = useAuth();
  const { canView, scope, scopeKey } = useReportBranchScope();
  const allowed =
    canView &&
    (params.branch_id ? scope === null || scope.includes(params.branch_id) : scope === null);
  return useQuery({
    queryKey: [
      "reports",
      "quarantine-on-date",
      user?.id,
      user?.active_tenant_id,
      user?.permissions,
      user?.permission_scopes,
      user?.support_access,
      scopeKey,
      params,
    ],
    queryFn: ({ signal }) => getQuarantineReport(params, signal),
    enabled: allowed,
  });
}
