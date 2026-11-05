import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { draftKey, loadDraft } from "@/features/pos/draftStorage";
import {
  clearPhysicalCartRecovery,
  loadPhysicalCartRecovery,
  savePhysicalCartRecovery,
  type PhysicalCartRecovery,
} from "@/features/pos/physicalCartRecovery";

import { authenticatePos, POS_SCOPE } from "./posIdentityFixture";

const operation: PhysicalCartRecovery = {
  kind: "save",
  itemId: "20000000-0000-4000-8000-000000000001",
  name: "Аспирин",
  payload: {
    catalog_id: "10000000-0000-4000-8000-000000000001",
    position: 1,
    sale_format: "blister",
    quantity: 2,
    discount_amount: "0.00",
  },
};

describe("physical cart recovery journal", () => {
  beforeEach(() => {
    authenticatePos();
    localStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());
  it("round-trips the precise unpaid operation payload and identity", () => {
    savePhysicalCartRecovery("sale-a", POS_SCOPE, operation);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toEqual(operation);
    authenticatePos({ tenantId: "t-1", userId: "u-2" });
    authenticatePos();
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toEqual(operation);
  });
  it.each([
    { tenantId: "t-2", userId: "u-1" },
    { tenantId: "t-1", userId: "u-2" },
  ])("does not expose or clear another scope %j", (scope) => {
    savePhysicalCartRecovery("sale-a", POS_SCOPE, operation);
    expect(loadPhysicalCartRecovery("sale-a", scope)).toBeNull();
    clearPhysicalCartRecovery("sale-a", scope);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toEqual(operation);
    expect(loadPhysicalCartRecovery("sale-b", POS_SCOPE)).toBeNull();
  });
  it("clears only the acknowledged sale marker", () => {
    savePhysicalCartRecovery("sale-a", POS_SCOPE, operation);
    savePhysicalCartRecovery("sale-b", POS_SCOPE, { kind: "delete", itemId: operation.itemId });
    clearPhysicalCartRecovery("sale-a", POS_SCOPE);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull();
    expect(loadPhysicalCartRecovery("sale-b", POS_SCOPE)).toEqual({
      kind: "delete",
      itemId: operation.itemId,
    });
  });
  it("preserves an expired draft address while an uncertain write remains", () => {
    savePhysicalCartRecovery("sale-a", POS_SCOPE, operation);
    localStorage.setItem(
      draftKey("register-a", POS_SCOPE),
      JSON.stringify({ saleId: "sale-a", nameById: {}, savedAt: Date.now() - 60 * 60_000 }),
    );
    expect(loadDraft("register-a", 30, POS_SCOPE)).toMatchObject({
      saleId: "sale-a",
      expired: false,
    });
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toEqual(operation);
    clearPhysicalCartRecovery("sale-a", POS_SCOPE);
    expect(loadDraft("register-a", 30, POS_SCOPE)).toMatchObject({ saleId: null, expired: true });
  });
  it("refuses invalid operation data before persistence", () => {
    expect(() =>
      savePhysicalCartRecovery("sale-a", POS_SCOPE, {
        ...operation,
        payload: { ...operation.payload, quantity: 0 },
      }),
    ).toThrow();
    expect(localStorage.length).toBe(0);
  });

  it.each([
    null,
    [],
    { kind: "delete", itemId: "invalid-id" },
    { ...operation, kind: "unknown" },
    { ...operation, name: 42 },
    { ...operation, payload: { ...operation.payload, quantity: "2" } },
    { ...operation, payload: { ...operation.payload, quantity: 1.5 } },
    { ...operation, payload: { ...operation.payload, quantity: 1_000_001 } },
    { ...operation, payload: { ...operation.payload, position: 0 } },
    { ...operation, payload: { ...operation.payload, catalog_id: "invalid-id" } },
    { ...operation, payload: { ...operation.payload, sale_format: "bottle" } },
    { ...operation, payload: { ...operation.payload, discount_amount: "-0.50" } },
  ])("retains malformed stored bytes without adopting them: %#", (value) => {
    savePhysicalCartRecovery("sale-a", POS_SCOPE, operation);
    const key = localStorage.key(0)!;
    const raw = JSON.stringify(value);
    localStorage.setItem(key, raw);
    expect(loadPhysicalCartRecovery("sale-a", POS_SCOPE)).toBeNull();
    expect(localStorage.getItem(key)).toBe(raw);
  });
});
