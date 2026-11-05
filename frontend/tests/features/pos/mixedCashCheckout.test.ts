import { createHash, webcrypto } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  completeMixedCash,
  getMixedCashOperation,
  parseMixedCashCheckoutResult,
  parseMixedCashOperationStatus,
  parseMixedCashQuote,
  parseSavedMixedCashIntent,
  saveMixedCashIntent,
} from "@/features/pos/mixedCashCheckout";
import { cashConfirmationRequestSchema } from "@/features/pos/physicalCashCheckout";
import { api, configureAuthHooks } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

import { authenticatePos } from "./posIdentityFixture";

const id = (suffix: number) => `00000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;
const scope = { tenantId: id(1), userId: id(2) };
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
  tenant_id: scope.tenantId,
  cashier_id: scope.userId,
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
const request = cashConfirmationRequestSchema.parse({
  schema_version: 2,
  operation_id: id(7),
  sale_id: quote.sale_id,
  expected_revision_hash: hash(quote),
  expected_total_amount: "0.32",
  cash_tendered: "1.00",
  currency: "TJS",
});
const saved = {
  schema_version: 2,
  state: "saved_draft",
  accepted_at: "2026-10-11T12:00:00Z",
  confirmation: {
    schema_version: 2,
    state: "validated_draft",
    request,
    quote,
    change_amount: "0.68",
  },
};
const result = {
  tenant_id: scope.tenantId,
  cashier_id: scope.userId,
  sale_id: quote.sale_id,
  branch_id: quote.branch_id,
  register_id: quote.register_id,
  shift_id: quote.shift_id,
  schema_version: 4,
  document_kind: "mixed_checkout",
  operation_id: request.operation_id,
  event_id: request.operation_id,
  stock_command_id: request.operation_id,
  created_at: "2026-10-11T11:00:00Z",
  completed_at: "2026-10-11T12:00:00Z",
  receipt_number: "M-1",
  receipt_sequence: 1,
  is_test: false,
  lines: [
    { ...legacyLine, name: "Дробная упаковка" },
    { ...physicalLine, name: "Таблетки" },
  ],
  payments: [{ id: request.operation_id, method: "cash", amount: "0.32", currency: "TJS" }],
  total_amount: "0.32",
  currency: "TJS",
};
function completedStatus(changed: unknown = result) {
  return {
    state: "completed",
    operation_id: request.operation_id,
    sale_id: request.sale_id,
    saved_intent: saved,
    result: changed,
  };
}
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

describe("mixed cash contracts", () => {
  it("accepts saved decimal packs with HALF_EVEN and integer physical units", async () => {
    expect(parseSavedMixedCashIntent(saved, request, scope)).toEqual(saved);
    expect(parseMixedCashCheckoutResult(result, request, scope)).toEqual(result);
    expect(parseMixedCashOperationStatus(completedStatus(), request, scope)).toEqual(
      completedStatus(),
    );
    expect(
      await parseMixedCashQuote(
        { quote, revision_hash: hash(quote) },
        quote.sale_id,
        quote.branch_id,
        quote.register_id,
        scope,
      ),
    ).toEqual({ quote, revision_hash: hash(quote) });
  });

  it.each(["legacy only", "physical only", "reordered", "positions", "duplicate ids", "extras"])(
    "rejects a structurally altered quote even with a matching hash: %s",
    async (variant) => {
      const modified = structuredClone(quote);
      if (variant === "legacy only") {
        modified.lines = [legacyLine, { ...legacyLine, id: id(33), position: 2 }];
        modified.total_amount = "0.04";
      }
      if (variant === "physical only") {
        modified.lines = [
          { ...physicalLine, position: 1 },
          { ...physicalLine, id: id(33), position: 2 },
        ];
        modified.total_amount = "0.60";
      }
      if (variant === "reordered") modified.lines.reverse();
      if (variant === "positions") modified.lines[1]!.position = 1;
      if (variant === "duplicate ids") modified.lines[1]!.id = legacyLine.id;
      const changed = variant === "extras" ? { ...modified, purchase_price: "1.00" } : modified;
      await expect(
        parseMixedCashQuote(
          { quote: changed, revision_hash: hash(changed) },
          quote.sale_id,
          quote.branch_id,
          quote.register_id,
          scope,
        ),
      ).rejects.toThrow();
    },
  );

  it.each([0.001, "0.000", "0.0001", "1e-3", "01.000", "-0.001", "Infinity"])(
    "rejects unsafe legacy quantity %s",
    (quantity) => {
      const changed = { ...result, lines: [{ ...result.lines[0], quantity }, result.lines[1]] };
      expect(() => parseMixedCashCheckoutResult(changed, request, scope)).toThrow();
    },
  );

  it("rejects physical-first order even with contiguous global positions and a valid hash", async () => {
    const changedQuote = {
      ...quote,
      lines: [
        { ...physicalLine, position: 1 },
        { ...legacyLine, position: 2 },
      ],
    };
    await expect(
      parseMixedCashQuote(
        { quote: changedQuote, revision_hash: hash(changedQuote) },
        quote.sale_id,
        quote.branch_id,
        quote.register_id,
        scope,
      ),
    ).rejects.toThrow();
    expect(() =>
      parseMixedCashCheckoutResult(
        {
          ...result,
          lines: [
            { ...physicalLine, position: 1, name: "Таблетки" },
            { ...legacyLine, position: 2, name: "Дробная упаковка" },
          ],
        },
        request,
        scope,
      ),
    ).toThrow();
  });

  it.each([
    ["5.00", "0.00", "0.30"],
    ["15.00", "0.02", "0.32"],
    ["25.00", "0.02", "0.32"],
    ["35.00", "0.04", "0.34"],
  ])("rounds fractional legacy %s exactly with HALF_EVEN", (price, gross, total) => {
    const changed = {
      ...result,
      lines: [
        {
          ...result.lines[0],
          original_retail_unit_price: price,
          gross_amount: gross,
          net_amount: gross,
        },
        result.lines[1],
      ],
      total_amount: total,
      payments: [{ ...result.payments[0], amount: total }],
    };
    expect(
      parseMixedCashCheckoutResult(changed, { ...request, expected_total_amount: total }, scope),
    ).toEqual(changed);
  });

  it.each(["rounding", "payment", "payment method", "physical units", "extra field", "test"])(
    "rejects forged receipt facts: %s",
    (variant) => {
      const changed = structuredClone(result);
      if (variant === "rounding") {
        changed.lines[0]!.gross_amount = "0.03";
        changed.lines[0]!.net_amount = "0.03";
        changed.total_amount = "0.33";
        changed.payments[0]!.amount = "0.33";
      }
      if (variant === "payment") changed.payments[0]!.amount = "0.33";
      if (variant === "payment method") changed.payments[0]!.method = "card";
      if (variant === "physical units") {
        const physical = changed.lines[1];
        if (physical && "base_units" in physical) physical.base_units = 2;
      }
      if (variant === "test") changed.is_test = true;
      const data = variant === "extra field" ? { ...changed, tenant_override: true } : changed;
      const matchingRequest =
        variant === "rounding" ? { ...request, expected_total_amount: "0.33" } : request;
      expect(() => parseMixedCashCheckoutResult(data, matchingRequest, scope)).toThrow();
    },
  );

  it.each(["tenant_id", "cashier_id", "sale_id", "operation_id", "event_id", "stock_command_id"])(
    "rejects a receipt belonging to another %s",
    (field) => {
      expect(() =>
        parseMixedCashCheckoutResult({ ...result, [field]: id(30) }, request, scope),
      ).toThrow();
    },
  );

  it.each(["catalog", "batch", "shift", "register", "branch"])(
    "rejects an internally balanced result diverging from saved %s",
    (variant) => {
      const changed = structuredClone(result);
      if (variant === "catalog") changed.lines[0]!.catalog_id = id(31);
      if (variant === "batch") {
        const legacy = changed.lines[0];
        if (legacy && "batch_id" in legacy) legacy.batch_id = id(31);
      }
      if (variant === "shift") changed.shift_id = id(31);
      if (variant === "register") changed.register_id = id(31);
      if (variant === "branch") changed.branch_id = id(31);
      expect(() =>
        parseMixedCashOperationStatus(completedStatus(changed), request, scope),
      ).toThrow();
    },
  );

  it("rejects changed fixed tender and impossible or foreign operation statuses", () => {
    expect(() =>
      parseSavedMixedCashIntent(saved, { ...request, cash_tendered: "2.00" }, scope),
    ).toThrow();
    for (const status of [
      { state: "unknown", operation_id: request.operation_id, sale_id: request.sale_id },
      { state: "not_found", operation_id: id(31), sale_id: request.sale_id },
      { state: "not_found", operation_id: request.operation_id, sale_id: id(31) },
      { state: "not_found", operation_id: request.operation_id, sale_id: request.sale_id, result },
    ])
      expect(() => parseMixedCashOperationStatus(status, request, scope)).toThrow();
  });
});

describe("mixed cash API", () => {
  it("uses the same fixed request and operation UUID on the dedicated endpoints", async () => {
    const seen: string[] = [];
    api.defaults.adapter = async (config) => {
      seen.push(`${config.method}:${config.url}`);
      if (config.method === "post") expect(JSON.parse(String(config.data))).toEqual(request);
      return {
        data: config.url?.endsWith("/intent")
          ? saved
          : config.url?.endsWith("/complete")
            ? result
            : completedStatus(),
        status: 200,
        statusText: "OK",
        headers: {},
        config,
      };
    };
    expect(await saveMixedCashIntent(request, scope)).toEqual(saved);
    expect(await completeMixedCash(request, scope)).toEqual(result);
    expect(await getMixedCashOperation(request, scope)).toEqual(completedStatus());
    expect(seen).toEqual([
      `post:/sales/${request.sale_id}/mixed-cash/intent`,
      `post:/sales/${request.sale_id}/mixed-cash/complete`,
      `get:/sales/${request.sale_id}/mixed-cash/operations/${request.operation_id}`,
    ]);
  });

  it("rejects a quote with a substituted revision or foreign workstation", async () => {
    for (const [revision, branch, register, owner] of [
      ["a".repeat(64), quote.branch_id, quote.register_id, scope],
      [hash(quote), id(31), quote.register_id, scope],
      [hash(quote), quote.branch_id, id(31), scope],
      [hash(quote), quote.branch_id, quote.register_id, { ...scope, userId: id(31) }],
    ] as const)
      await expect(
        parseMixedCashQuote(
          { quote, revision_hash: revision },
          quote.sale_id,
          branch,
          register,
          owner,
        ),
      ).rejects.toThrow();
  });

  it.each(["save", "status"])(
    "rejects internally valid saved quote hash tampering on %s",
    async (phase) => {
      const changedRequest = { ...request, expected_revision_hash: "a".repeat(64) };
      const changedSaved = {
        ...saved,
        confirmation: { ...saved.confirmation, request: changedRequest },
      };
      api.defaults.adapter = async (config) => ({
        data:
          phase === "save" ? changedSaved : { ...completedStatus(), saved_intent: changedSaved },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
      });
      await expect(
        phase === "save"
          ? saveMixedCashIntent(changedRequest, scope)
          : getMixedCashOperation(changedRequest, scope),
      ).rejects.toThrow();
    },
  );

  it("rejects a late response after session change and sends no subsequent write", async () => {
    const spy = vi.fn(async (config) => {
      authenticatePos({ ...scope, userId: id(30) });
      return { data: result, status: 200, statusText: "OK", headers: {}, config };
    });
    api.defaults.adapter = spy;
    await expect(completeMixedCash(request, scope)).rejects.toThrow(
      "Authentication session changed",
    );
    await expect(saveMixedCashIntent(request, scope)).rejects.toThrow(
      "Authentication session changed",
    );
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
