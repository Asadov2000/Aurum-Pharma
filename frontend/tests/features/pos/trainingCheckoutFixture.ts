import type { TrainingCheckoutRequest } from "@/features/pos/trainingCheckout";
import { createHash } from "node:crypto";
const id = (suffix: number) => `00000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;
const ownerScope = { tenantId: id(1), userId: id(2) };
const legacyLine = {
  inventory_kind: "legacy_pack" as const,
  id: id(8),
  position: 1,
  catalog_id: id(9),
  batch_id: id(10),
  quantity: "0.001",
  original_retail_unit_price: "25.00",
  gross_amount: "0.02",
  discount_amount: "0.00",
  net_amount: "0.02",
  money_version: 1 as const,
  currency: "TJS" as const,
};
const physicalLine = {
  inventory_kind: "physical_unit" as const,
  id: id(11),
  position: 2,
  catalog_id: id(12),
  packaging: {
    version: 2 as const,
    unit: "tablet" as const,
    units_per_pack: 20,
    units_per_blister: 10,
    dispense_step: 1,
  },
  sale_format: "unit" as const,
  quantity: 3,
  base_units: 3,
  original_retail_unit_price: "0.10",
  gross_amount: "0.30",
  discount_amount: "0.00",
  net_amount: "0.30",
  money_version: 1 as const,
  currency: "TJS" as const,
};
const quote = {
  schema_version: 2 as const,
  state: "draft_quote" as const,
  tenant_id: ownerScope.tenantId,
  cashier_id: ownerScope.userId,
  sale_id: id(3),
  branch_id: id(4),
  register_id: id(5),
  shift_id: id(6),
  lines: [legacyLine, physicalLine],
  total_amount: "0.32",
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
function hash(value: unknown): string {
  return createHash("sha256").update(canonical(value)).digest("hex");
}

export function trainingFixture(mixed = true) {
  const q = structuredClone(quote);
  if (!mixed) {
    q.lines = [{ ...physicalLine, position: 1 }];
    q.total_amount = "0.30";
  }
  const scope = {
    ...ownerScope,
    saleId: q.sale_id,
    branchId: q.branch_id,
    registerId: q.register_id,
  };
  const request: TrainingCheckoutRequest = {
    schema_version: 1 as const,
    operation_id: id(7),
    sale_id: q.sale_id,
    expected_revision_hash: hash(q),
    expected_total_amount: q.total_amount,
    simulated_payments: [{ method: "cash" as const, amount: q.total_amount }],
    cash_tendered: "1.00",
    currency: "TJS" as const,
  };
  const headers = {
    schema_version: 1 as const,
    is_test: true as const,
    operation_id: request.operation_id,
    sale_id: q.sale_id,
    tenant_id: q.tenant_id,
    branch_id: q.branch_id,
    register_id: q.register_id,
    shift_id: q.shift_id,
    cashier_id: q.cashier_id,
    receipt_number: "TEST-1",
    receipt_sequence: 1,
    completed_at: "2026-10-27T00:00:00Z",
    total_amount: q.total_amount,
    currency: "TJS" as const,
  };
  const lines = q.lines.map((line) => {
    const { id: unusedId, catalog_id: unusedCatalog, ...rest } = line;
    void unusedId;
    void unusedCatalog;
    if (rest.inventory_kind === "legacy_pack") {
      const { batch_id: unusedBatch, ...facts } = rest;
      void unusedBatch;
      return { ...facts, name: "Legacy" };
    }
    return { ...rest, name: "Physical", unit_label: "табл." };
  });
  const result = {
    ...headers,
    document_kind: "training_checkout" as const,
    request,
    receipt: {
      ...headers,
      document_kind: "training_sale_receipt" as const,
      lines,
      simulated_payments: request.simulated_payments,
      cash_tendered: request.cash_tendered,
      change_amount: mixed ? "0.68" : "0.70",
    },
    simulation: {
      schema_version: 1 as const,
      state: "simulated_training_sale" as const,
      is_test: true as const,
      stock_date: "2026-10-27",
      quote: q,
      total_amount: q.total_amount,
      physical_before: [],
      physical_after: [],
      legacy_before: [],
      legacy_after: [],
    },
  };
  return { scope, request, result, quote: { quote: q, revision_hash: hash(q) } };
}
