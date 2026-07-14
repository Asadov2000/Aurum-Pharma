import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingCheckoutOperation,
  createPendingCheckoutOperation,
  loadPendingCheckoutOperation,
} from "@/features/pos/checkoutOperation";
import { draftKey, loadDraft } from "@/features/pos/draftStorage";

describe("pending POS checkout operation", () => {
  beforeEach(() => window.localStorage.clear());

  it("persists only safe identifiers and clears only a matching UUIDv4 operation", () => {
    const operation = createPendingCheckoutOperation("sale-1", "register-1");

    expect(operation).not.toBeNull();
    if (!operation) throw new Error("operation was not persisted");
    expect(operation.operationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(loadPendingCheckoutOperation("sale-1", "register-1")).toEqual(operation);
    expect(JSON.stringify(operation)).not.toMatch(/amount|patient|prescription|payment/i);

    clearPendingCheckoutOperation("sale-1", crypto.randomUUID());
    expect(loadPendingCheckoutOperation("sale-1", "register-1")).toEqual(operation);

    clearPendingCheckoutOperation("sale-1", operation.operationId);
    expect(loadPendingCheckoutOperation("sale-1", "register-1")).toBeNull();
  });

  it("rejects a marker bound to another register", () => {
    const operation = createPendingCheckoutOperation("sale-1", "register-1");
    if (!operation) throw new Error("operation was not persisted");

    expect(loadPendingCheckoutOperation("sale-1", "register-2")).toBeNull();
    expect(loadPendingCheckoutOperation("sale-1")).toEqual(operation);
  });

  it("does not send a retry key to unreliable storage", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    try {
      expect(createPendingCheckoutOperation("sale-1", "register-1")).toBeNull();
    } finally {
      setItem.mockRestore();
    }
  });

  it("keeps an expired draft while checkout reconciliation is unresolved", () => {
    window.localStorage.setItem(
      draftKey("register-1"),
      JSON.stringify({ saleId: "sale-1", nameById: {}, savedAt: 0 }),
    );
    const operation = createPendingCheckoutOperation("sale-1", "register-1");
    if (!operation) throw new Error("operation was not persisted");

    expect(loadDraft("register-1", 1)).toEqual({
      saleId: "sale-1",
      nameById: {},
      expired: false,
      requiresRx: false,
    });

    clearPendingCheckoutOperation("sale-1", operation.operationId);
    expect(loadDraft("register-1", 1)).toEqual({
      saleId: null,
      nameById: {},
      expired: true,
      requiresRx: false,
    });
  });
});
