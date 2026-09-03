import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPaymentAttemptOperation,
  createPaymentAttemptOperation,
  loadPaymentAttemptOperation,
  saveRecoveredPaymentAttemptOperation,
} from "@/features/pos/paymentAttemptOperation";

const SALE_ID = "10000000-0000-4000-8000-000000000001";

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

  it("rebuilds an opaque marker from a server-owned active attempt", () => {
    const operation = saveRecoveredPaymentAttemptOperation({
      id: "20000000-0000-4000-8000-000000000001",
      tenant_id: "30000000-0000-4000-8000-000000000001",
      sale_id: SALE_ID,
      cashier_user_id: "40000000-0000-4000-8000-000000000001",
      operation_id: "50000000-0000-4000-8000-000000000001",
      payment_method: "card",
      amount: "50.00",
      currency: "TJS",
      status: "confirmed",
      terminal_id: "TERM-01",
      external_reference: "DOC-01",
      resolved_by_user_id: "40000000-0000-4000-8000-000000000001",
      reconciliation_started_at: "2026-09-04T10:00:00Z",
      evidence_required: true,
      void_reason: null,
      void_note: null,
      created_at: "2026-09-04T10:00:00Z",
      confirmed_at: "2026-09-04T10:01:00Z",
      consumed_at: null,
      voided_at: null,
    });

    expect(operation).toEqual({
      operationId: "50000000-0000-4000-8000-000000000001",
      saleId: SALE_ID,
      paymentMethod: "card",
      amount: "50.00",
    });
    expect(loadPaymentAttemptOperation(SALE_ID)).toEqual(operation);
    expect(window.localStorage.getItem(`pos:pendingPaymentAttempt:${SALE_ID}`)).not.toContain(
      "DOC-01",
    );
  });
});
