import { beforeEach, describe, expect, it, vi } from "vitest";

import { draftKey, loadDraft } from "@/features/pos/draftStorage";
import {
  clearPendingPaymentOperation,
  createPendingPaymentOperation,
  loadPendingPaymentOperation,
} from "@/features/pos/paymentOperation";

describe("pending POS payment operation", () => {
  beforeEach(() => window.localStorage.clear());

  it("persists one UUIDv4 operation and clears only the matching operation", () => {
    const operation = createPendingPaymentOperation("sale-1", "cash", "50.00");

    expect(operation).not.toBeNull();
    if (!operation) throw new Error("operation was not persisted");
    expect(operation.operationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(loadPendingPaymentOperation("sale-1")).toEqual(operation);

    clearPendingPaymentOperation("sale-1", crypto.randomUUID());
    expect(loadPendingPaymentOperation("sale-1")).toEqual(operation);

    clearPendingPaymentOperation("sale-1", operation.operationId);
    expect(loadPendingPaymentOperation("sale-1")).toBeNull();
  });

  it("does not create a retry key when durable storage fails", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });

    expect(createPendingPaymentOperation("sale-1", "cash", "50.00")).toBeNull();
    setItem.mockRestore();
  });

  it("keeps an expired draft while a payment result is unresolved", () => {
    window.localStorage.setItem(
      draftKey("register-1"),
      JSON.stringify({ saleId: "sale-1", nameById: {}, savedAt: 0 }),
    );
    const operation = createPendingPaymentOperation("sale-1", "card", "20.00");
    if (!operation) throw new Error("operation was not persisted");

    expect(loadDraft("register-1", 1)).toEqual({
      saleId: "sale-1",
      nameById: {},
      expired: false,
      requiresRx: false,
    });

    clearPendingPaymentOperation("sale-1", operation.operationId);
    expect(loadDraft("register-1", 1)).toEqual({
      saleId: null,
      nameById: {},
      expired: true,
      requiresRx: false,
    });
  });
});
