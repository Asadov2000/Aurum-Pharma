import {
  type ElectronicCheckoutContext,
  type ElectronicAttempt,
} from "@/features/pos/unitElectronicCheckout";
import { unitPaymentRequestSchema } from "@/features/pos/unitPaymentPreparation";
import { context, attempt, result } from "./unitElectronicCheckoutFixture";
import { bound, hash, id, plan, quote, saved, scope } from "./unitPaymentPreparationFixture";

export function mixedContext(
  method: "card" | "qr" | "combined" = "card",
): ElectronicCheckoutContext {
  const mixedQuote = {
    ...quote,
    lines: [
      {
        inventory_kind: "legacy_pack" as const,
        id: id(30),
        position: 1,
        catalog_id: id(31),
        batch_id: id(32),
        quantity: "0.001",
        original_retail_unit_price: "25.00",
        gross_amount: "0.02",
        discount_amount: "0.00",
        net_amount: "0.02",
        money_version: 1 as const,
        currency: "TJS" as const,
      },
      { ...quote.lines[0]!, position: 2 },
    ],
    total_amount: "2.01",
  };
  const payments =
    method === "combined"
      ? [
          { method: "card", amount: "1.00" },
          { method: "cash", amount: "0.01" },
          { method: "qr", amount: "1.00" },
        ]
      : [{ method, amount: "2.01" }];
  const request = unitPaymentRequestSchema.parse({
    sale_id: mixedQuote.sale_id,
    expected_revision_hash: hash(mixedQuote),
    expected_total_amount: "2.01",
    currency: "TJS",
    payments,
    cash_tendered: method === "combined" ? "1.00" : null,
  });
  const planHash = hash({
    kind: "unit_payment_plan_v1",
    tenant_id: scope.tenantId,
    branch_id: quote.branch_id,
    cashier_id: scope.userId,
    sale_id: quote.sale_id,
    revision_hash: request.expected_revision_hash,
    currency: "TJS",
    payments: request.payments,
    cash_tendered: request.cash_tendered,
  });
  return {
    ...context(),
    intent: {
      ...saved,
      plan: {
        ...plan,
        request,
        quote: mixedQuote,
        plan_hash: planHash,
        change_amount: method === "combined" ? "0.99" : "0.00",
      },
    },
    binding: { ...bound, plan_hash: planHash },
  };
}

export function mixedAttempts(
  c: ElectronicCheckoutContext,
  status: ElectronicAttempt["status"] = "consumed",
): ElectronicAttempt[] {
  return c.intent.plan.request.payments
    .filter((p) => p.method !== "cash")
    .map((p) => ({
      ...attempt(status),
      id: id(p.method === "card" ? 21 : 22),
      operation_id: id(p.method === "card" ? 21 : 22),
      payment_method: p.method === "card" ? "card" : "qr",
      amount: p.amount,
    }));
}

export function mixedResult(c: ElectronicCheckoutContext) {
  const rows = mixedAttempts(c);
  return {
    ...result(),
    schema_version: 4 as const,
    document_kind: "mixed_checkout" as const,
    lines: c.intent.plan.quote.lines.map((line) => ({ ...line, name: "Historical name" })),
    payments: c.intent.plan.request.payments.map((p) => ({
      id:
        p.method === "cash"
          ? c.intent.operation_id
          : rows.find((r) => r.payment_method === p.method)!.id,
      method: p.method,
      amount: p.amount,
      currency: "TJS" as const,
    })),
    total_amount: "2.01",
  };
}
