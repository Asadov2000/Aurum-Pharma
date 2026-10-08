import { createHash, webcrypto } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cashConfirmationRequestSchema,
  completePhysicalCash,
  getPhysicalCashOperation,
  parsePhysicalCashCheckoutResult,
  parsePhysicalCashOperationStatus,
  parseSavedPhysicalCashIntent,
  savePhysicalCashIntent,
  type CashConfirmationRequest,
} from "@/features/pos/physicalCashCheckout";
import { api, configureAuthHooks } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

import { authenticatePos } from "./features/pos/posIdentityFixture";

const id = (suffix: number) => `00000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;
const scope = { tenantId: id(1), userId: id(2) };
const line = {
  inventory_kind: "physical_unit" as const,
  id: id(8),
  position: 1,
  catalog_id: id(9),
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
  tenant_id: scope.tenantId,
  cashier_id: scope.userId,
  sale_id: id(3),
  branch_id: id(4),
  register_id: id(5),
  shift_id: id(6),
  lines: [line],
  total_amount: "0.30",
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
const request: CashConfirmationRequest = {
  schema_version: 2,
  operation_id: id(7),
  sale_id: quote.sale_id,
  expected_revision_hash: createHash("sha256").update(canonical(quote)).digest("hex"),
  expected_total_amount: "0.30",
  cash_tendered: "1.00",
  currency: "TJS",
};
const saved = {
  schema_version: 2,
  state: "saved_draft",
  accepted_at: "2026-10-08T12:00:00Z",
  confirmation: {
    schema_version: 2,
    state: "validated_draft",
    request,
    quote,
    change_amount: "0.70",
  },
};
const result = {
  tenant_id: scope.tenantId,
  cashier_id: scope.userId,
  sale_id: quote.sale_id,
  branch_id: quote.branch_id,
  register_id: quote.register_id,
  shift_id: quote.shift_id,
  schema_version: 3,
  document_kind: "physical_checkout",
  operation_id: request.operation_id,
  event_id: id(10),
  stock_command_id: id(11),
  created_at: "2026-10-08T11:00:00Z",
  completed_at: "2026-10-08T12:00:00Z",
  receipt_number: "T-1",
  receipt_sequence: 1,
  is_test: true,
  lines: [{ ...line, name: "Тестовый товар" }],
  payments: [{ id: id(12), method: "cash", amount: "0.30", currency: "TJS" }],
  total_amount: "0.30",
  currency: "TJS",
};
const adapter = api.defaults.adapter;
beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  authenticatePos(scope);
  useAuthStore.setState({ accessToken: "synthetic-access" });
  configureAuthHooks({
    getAccessToken: () => useAuthStore.getState().accessToken,
    refreshTokens: async () => null,
    requestMfaStepUp: async () => null,
    onAuthFailure: () => {},
  });
});
afterEach(() => {
  api.defaults.adapter = adapter;
  useAuthStore.getState().clear();
  configureAuthHooks({
    getAccessToken: () => null,
    refreshTokens: async () => null,
    requestMfaStepUp: async () => null,
    onAuthFailure: () => {},
  });
  vi.unstubAllGlobals();
});
describe("physical cash contracts", () => {
  it.each(["0.00", "-1.00", "1e2", "Infinity", "1.001", "01.00", 1, true])(
    "rejects unsafe cash %s without throwing outside Zod",
    (cash) => {
      expect(
        cashConfirmationRequestSchema.safeParse({ ...request, cash_tendered: cash }).success,
      ).toBe(false);
    },
  );
  it("rejects client-owned fields and non-v4 operations", () => {
    expect(
      cashConfirmationRequestSchema.safeParse({ ...request, tenant_id: scope.tenantId }).success,
    ).toBe(false);
    expect(
      cashConfirmationRequestSchema.safeParse({
        ...request,
        operation_id: "00000000-0000-1000-8000-000000000007",
      }).success,
    ).toBe(false);
  });
  it("validates exact small decimal balances and all three status states", () => {
    expect(parseSavedPhysicalCashIntent(saved, request, scope)).toEqual(saved);
    expect(parsePhysicalCashCheckoutResult(result, request, scope)).toEqual(result);
    for (const status of [
      { state: "not_found", operation_id: request.operation_id, sale_id: request.sale_id },
      {
        state: "saved_draft",
        operation_id: request.operation_id,
        sale_id: request.sale_id,
        saved_intent: saved,
      },
      {
        state: "completed",
        operation_id: request.operation_id,
        sale_id: request.sale_id,
        saved_intent: saved,
        result,
      },
    ])
      expect(parsePhysicalCashOperationStatus(status, request, scope)).toEqual(status);
  });
  it("rejects another owner, fixed request, operation and impossible state payload", () => {
    expect(() =>
      parseSavedPhysicalCashIntent(saved, request, { ...scope, userId: id(20) }),
    ).toThrow();
    expect(() =>
      parseSavedPhysicalCashIntent(saved, { ...request, cash_tendered: "2.00" }, scope),
    ).toThrow();
    expect(() =>
      parsePhysicalCashCheckoutResult({ ...result, operation_id: id(20) }, request, scope),
    ).toThrow();
    expect(() =>
      parsePhysicalCashOperationStatus(
        {
          state: "not_found",
          operation_id: request.operation_id,
          sale_id: request.sale_id,
          result,
        },
        request,
        scope,
      ),
    ).toThrow();
  });
  it.each(["payment", "composition", "amount", "positions", "timestamp", "fractional quantity"])(
    "rejects invalid receipt %s",
    (variant) => {
      const modified = structuredClone(result);
      if (variant === "payment") modified.payments[0]!.amount = "0.31";
      if (variant === "composition") modified.lines[0]!.base_units = 2;
      if (variant === "amount") modified.lines[0]!.net_amount = "0.31";
      if (variant === "positions") modified.lines[0]!.position = 2;
      if (variant === "timestamp") modified.completed_at = "2026-10-07T12:00:00Z";
      if (variant === "fractional quantity") modified.lines[0]!.quantity = 0.3;
      expect(() => parsePhysicalCashCheckoutResult(modified, request, scope)).toThrow();
    },
  );
  it("rejects internally balanced result whose original catalog or shift changed", () => {
    for (const changed of [
      { ...result, shift_id: id(22) },
      { ...result, lines: [{ ...result.lines[0], catalog_id: id(22) }] },
    ])
      expect(() =>
        parsePhysicalCashOperationStatus(
          {
            state: "completed",
            operation_id: request.operation_id,
            sale_id: request.sale_id,
            saved_intent: saved,
            result: changed,
          },
          request,
          scope,
        ),
      ).toThrow();
  });
});
describe("physical cash API", () => {
  it("uses the fixed complete request for both writes and fixed operation for recovery", async () => {
    const seen: string[] = [];
    api.defaults.adapter = async (config) => {
      seen.push(`${config.method}:${config.url}`);
      if (config.method === "post") expect(JSON.parse(String(config.data))).toEqual(request);
      return {
        data: config.url?.endsWith("/intent")
          ? saved
          : config.url?.endsWith("/complete")
            ? result
            : {
                state: "completed",
                operation_id: request.operation_id,
                sale_id: request.sale_id,
                saved_intent: saved,
                result,
              },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
      };
    };
    await savePhysicalCashIntent(request, scope);
    await completePhysicalCash(request, scope);
    await getPhysicalCashOperation(request, scope);
    expect(seen).toEqual([
      `post:/sales/${request.sale_id}/physical-cash/intent`,
      `post:/sales/${request.sale_id}/physical-cash/complete`,
      `get:/sales/${request.sale_id}/physical-cash/operations/${request.operation_id}`,
    ]);
  });
  it("rejects an internally coherent quote with a substituted revision", async () => {
    const changed = { ...request, expected_revision_hash: "a".repeat(64) };
    api.defaults.adapter = async (config) => ({
      data: { ...saved, confirmation: { ...saved.confirmation, request: changed } },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });
    await expect(savePhysicalCashIntent(changed, scope)).rejects.toThrow("Сохранённая корзина");
  });
  it("does not send a write after account change and rejects late responses", async () => {
    const spy = vi.fn(async (config) => {
      authenticatePos({ ...scope, userId: id(30) });
      return { data: result, status: 200, statusText: "OK", headers: {}, config };
    });
    api.defaults.adapter = spy;
    await expect(completePhysicalCash(request, scope)).rejects.toThrow(
      "Authentication session changed",
    );
    await expect(savePhysicalCashIntent(request, scope)).rejects.toThrow(
      "Authentication session changed",
    );
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
