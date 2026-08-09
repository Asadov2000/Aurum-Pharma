import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPaymentAttemptOperation,
  createPaymentAttemptOperation,
  loadPaymentAttemptOperation,
} from "@/features/pos/paymentAttemptOperation";

const SALE_ID = "sale-1";

describe("paymentAttemptOperation", () => {
  beforeEach(() => window.localStorage.clear());

  it("reuses the durable operation id for an identical retry", () => {
    const first = createPaymentAttemptOperation(SALE_ID, "card", "50.00");
    const second = createPaymentAttemptOperation(SALE_ID, "card", "50.00");

    expect(first).not.toBeNull();
    expect(second).toEqual(first);
    expect(loadPaymentAttemptOperation(SALE_ID)).toEqual(first);
  });

  it("keeps an unresolved marker when method or amount changes", () => {
    const first = createPaymentAttemptOperation(SALE_ID, "card", "50.00");
    const second = createPaymentAttemptOperation(SALE_ID, "qr", "40.00");

    expect(second).toBeNull();
    expect(loadPaymentAttemptOperation(SALE_ID)).toEqual(first);
  });

  it("clears only the matching operation", () => {
    const operation = createPaymentAttemptOperation(SALE_ID, "card", "50.00");
    expect(operation).not.toBeNull();

    clearPaymentAttemptOperation(SALE_ID, "90000000-0000-4000-8000-000000000001");
    expect(loadPaymentAttemptOperation(SALE_ID)).toEqual(operation);

    clearPaymentAttemptOperation(SALE_ID, operation?.operationId);
    expect(loadPaymentAttemptOperation(SALE_ID)).toBeNull();
  });

  it("fails closed when localStorage cannot persist the marker", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    try {
      expect(createPaymentAttemptOperation(SALE_ID, "card", "50.00")).toBeNull();
    } finally {
      setItem.mockRestore();
    }
  });
});
