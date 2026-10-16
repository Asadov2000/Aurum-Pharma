import { webcrypto } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getActiveUnitPayment,
  getUnitPaymentIntent,
  getUnitPaymentQuote,
  normalizePaymentMoney,
  parseSavedUnitPaymentIntent,
  parseUnitPaymentBinding,
  parseUnitPaymentPlan,
  previewUnitPayment,
  saveUnitPaymentIntent,
  unitPaymentBindingAction,
  unitPaymentRequestSchema,
} from "@/features/pos/unitPaymentPreparation";
import {
  hasUnitPaymentPreparation,
  unitPaymentMarkerKey,
} from "@/features/pos/unitPaymentPreparationPresence";
import {
  clearUnitPaymentMarker,
  loadUnitPaymentMarker,
  storeUnitPaymentMarker,
} from "@/features/pos/unitPaymentPreparationStorage";
import { api } from "@/lib/api";

import { authenticatePos } from "./posIdentityFixture";
import {
  bound,
  cancelled,
  id,
  plan,
  quote,
  request,
  saleId,
  saved,
  scope,
} from "./unitPaymentPreparationFixture";

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  localStorage.clear();
  authenticatePos(scope);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("unit payment preparation contract", () => {
  it("rejects a stale cashier's saved plan before sending a mutation", async () => {
    const nextScope = { ...scope, userId: id(99) };
    authenticatePos(nextScope);
    const post = vi.spyOn(api, "post");
    await expect(saveUnitPaymentIntent(saved.operation_id, plan, nextScope)).rejects.toThrow();
    await expect(unitPaymentBindingAction(saved, "cancel-binding", nextScope)).rejects.toThrow();
    expect(post).not.toHaveBeenCalled();
  });
  it("validates the full server quote and plan fingerprint", async () => {
    expect(await parseUnitPaymentPlan(plan, saleId, scope)).toEqual(plan);
    expect(await parseSavedUnitPaymentIntent(saved, saleId, scope, saved.operation_id)).toEqual(
      saved,
    );
    await expect(
      parseUnitPaymentPlan({ ...plan, plan_hash: "a".repeat(64) }, saleId, scope),
    ).rejects.toThrow();
    await expect(parseUnitPaymentPlan(plan, id(99), scope)).rejects.toThrow();
    await expect(
      parseUnitPaymentPlan(plan, saleId, { ...scope, tenantId: id(99) }),
    ).rejects.toThrow();
    await expect(parseSavedUnitPaymentIntent(saved, saleId, scope, id(99))).rejects.toThrow();
    expect(() => parseUnitPaymentBinding({ ...bound, plan_hash: "b".repeat(64) }, saved)).toThrow();
    expect(() =>
      parseUnitPaymentBinding({ ...cancelled, cancelled_at: "2026-10-20T11:00:00Z" }, saved),
    ).toThrow();
  });
  it("uses exact decimal splits and rejects cash-only, duplicate and short tenders", () => {
    expect(normalizePaymentMoney("0001,9")).toBe("1.90");
    expect(normalizePaymentMoney("999999999999.99")).toBe("999999999999.99");
    for (const payments of [
      [],
      [{ method: "card", amount: "1.98" }],
      [{ method: "cash", amount: "1.99" }],
      [
        { method: "card", amount: "1.00" },
        { method: "card", amount: "0.99" },
      ],
    ]) {
      expect(unitPaymentRequestSchema.safeParse({ ...request, payments }).success).toBe(false);
    }
    expect(
      unitPaymentRequestSchema.safeParse({
        ...request,
        payments: [
          { method: "cash", amount: "0.99" },
          { method: "qr", amount: "1.00" },
        ],
        cash_tendered: "0.98",
      }).success,
    ).toBe(false);
    expect(() => normalizePaymentMoney("1.999")).toThrow();
  });
  it("binds every network request to the captured auth/support context", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValueOnce({ data: plan })
      .mockResolvedValueOnce({ data: saved })
      .mockResolvedValueOnce({ data: bound });
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce({ data: { quote, revision_hash: request.expected_revision_hash } })
      .mockResolvedValueOnce({ data: saved })
      .mockResolvedValueOnce({ data: { sale_id: saleId, intent: saved, binding: bound } });
    await getUnitPaymentQuote(saleId, scope);
    await previewUnitPayment(request, scope);
    await saveUnitPaymentIntent(saved.operation_id, plan, scope);
    await getUnitPaymentIntent(saleId, saved.operation_id, scope);
    await getActiveUnitPayment(saleId, scope);
    await unitPaymentBindingAction(saved, "bind", scope);
    for (const call of post.mock.calls)
      expect(call[2]).toMatchObject({
        _auth: expect.any(Object),
        _supportRevision: expect.any(Number),
      });
    for (const call of get.mock.calls)
      expect(call[1]).toMatchObject({
        _auth: expect.any(Object),
        _supportRevision: expect.any(Number),
      });
  });
  it("rejects partial or cancelled active bindings", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      data: { sale_id: saleId, intent: saved, binding: cancelled },
    });
    await expect(getActiveUnitPayment(saleId, scope)).rejects.toThrow();
  });
});

describe("opaque recovery marker", () => {
  it("stores only the operation UUID and isolates users and sales", () => {
    storeUnitPaymentMarker(saleId, scope, saved.operation_id);
    expect(localStorage.getItem(unitPaymentMarkerKey(saleId, scope))).toBe(saved.operation_id);
    expect(loadUnitPaymentMarker(saleId, { ...scope, userId: id(99) })).toBeNull();
    expect(() => storeUnitPaymentMarker(saleId, scope, id(98))).toThrow();
    clearUnitPaymentMarker(saleId, scope, id(98));
    expect(hasUnitPaymentPreparation(saleId, scope)).toBe(true);
    clearUnitPaymentMarker(saleId, scope, saved.operation_id);
    expect(hasUnitPaymentPreparation(saleId, scope)).toBe(false);
  });
  it("fails closed for corrupt or unavailable storage", () => {
    localStorage.setItem(unitPaymentMarkerKey(saleId, scope), "broken");
    expect(hasUnitPaymentPreparation(saleId, scope)).toBe(true);
    expect(() => loadUnitPaymentMarker(saleId, scope)).toThrow();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage denied");
    });
    expect(hasUnitPaymentPreparation(saleId, scope)).toBe(true);
    expect(() => storeUnitPaymentMarker(saleId, scope, id(9))).toThrow();
  });
});
