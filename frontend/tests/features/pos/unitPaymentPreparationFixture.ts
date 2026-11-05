import { createHash } from "node:crypto";

import { unitPaymentRequestSchema } from "@/features/pos/unitPaymentPreparation";

export const id = (value: number) => `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
export const scope = { tenantId: id(1), userId: id(2) };
export const saleId = id(3);
export const quote = {
  schema_version: 2 as const,
  state: "draft_quote" as const,
  tenant_id: scope.tenantId,
  cashier_id: scope.userId,
  sale_id: saleId,
  branch_id: id(4),
  register_id: id(5),
  shift_id: id(6),
  lines: [
    {
      inventory_kind: "physical_unit" as const,
      id: id(7),
      position: 1,
      catalog_id: id(8),
      packaging: {
        version: 2 as const,
        unit: "tablet" as const,
        units_per_pack: 20,
        units_per_blister: 10,
        dispense_step: 1,
      },
      sale_format: "unit" as const,
      quantity: 2,
      base_units: 2,
      original_retail_unit_price: "1.00",
      gross_amount: "2.00",
      discount_amount: "0.01",
      net_amount: "1.99",
      money_version: 1 as const,
      currency: "TJS" as const,
    },
  ],
  total_amount: "1.99",
  currency: "TJS" as const,
};
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
export const hash = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");
export const request = unitPaymentRequestSchema.parse({
  sale_id: saleId,
  expected_revision_hash: hash(quote),
  expected_total_amount: "1.99",
  currency: "TJS",
  payments: [{ method: "card", amount: "1.99" }],
  cash_tendered: null,
});
export const plan = {
  state: "validated_draft" as const,
  request,
  quote,
  change_amount: "0.00",
  plan_hash: hash({
    kind: "unit_payment_plan_v1",
    tenant_id: scope.tenantId,
    branch_id: quote.branch_id,
    cashier_id: scope.userId,
    sale_id: saleId,
    revision_hash: request.expected_revision_hash,
    currency: "TJS",
    payments: request.payments,
    cash_tendered: null,
  }),
};
export const saved = {
  state: "saved_draft" as const,
  operation_id: id(9),
  saved_at: "2026-10-20T12:00:00Z",
  plan,
};
export const bound = {
  state: "bound" as const,
  operation_id: id(9),
  sale_id: saleId,
  plan_hash: plan.plan_hash,
  bound_at: "2026-10-20T12:01:00Z",
  cancelled_at: null,
};
export const cancelled = {
  ...bound,
  state: "cancelled" as const,
  cancelled_at: "2026-10-20T12:02:00Z",
};
