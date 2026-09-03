import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingRefundOperation,
  createPendingRefundOperation,
  loadPendingRefundOperation,
  pendingRefundOperationKey,
  saveRecoveredPendingRefundOperation,
} from "@/features/sales/refundOperation";
import { type RefundAttempt } from "@/features/sales/types";

const SERVER_ATTEMPT: RefundAttempt = {
  id: "attempt-1",
  tenant_id: "tenant-1",
  parent_sale_id: "sale-1",
  register_id: "register-1",
  requested_by_user_id: "user-1",
  confirmed_by_user_id: null,
  operation_id: "11111111-1111-4111-8111-111111111111",
  items: [{ sale_item_id: "item-1", qty: "1" }],
  payments: [],
  total_amount: "10.00",
  external_amount: "10.00",
  currency: "TJS",
  status: "pending",
  void_reason: null,
  void_note: null,
  created_at: "2026-08-09T10:00:00Z",
  confirmed_at: null,
  consumed_at: null,
  voided_at: null,
};

describe("pending refund operation", () => {
  beforeEach(() => window.localStorage.clear());

  it("persists one UUIDv4 marker without refund or customer details", () => {
    const operation = createPendingRefundOperation(
      "sale-1",
      [{ sale_item_id: "item-1", qty: "1.000" }],
      true,
    );

    expect(operation).not.toBeNull();
    expect(operation?.operationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(operation?.refundAttemptOperationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    expect(operation?.refundAttemptId).toBeNull();
    expect(
      createPendingRefundOperation("sale-1", [{ sale_item_id: "different", qty: "99" }], false),
    ).toEqual(operation);
    expect(loadPendingRefundOperation("sale-1")).toEqual(operation);
    expect(
      JSON.parse(window.localStorage.getItem(pendingRefundOperationKey("sale-1")) ?? "{}"),
    ).toEqual(operation);
    const serialized = JSON.stringify(operation);
    expect(serialized).not.toContain("reason");
    expect(serialized).not.toContain("comment");
  });

  it("clears only the matching operation", () => {
    const operation = createPendingRefundOperation(
      "sale-1",
      [{ sale_item_id: "item-1", qty: "1" }],
      false,
    );
    if (!operation) throw new Error("refund operation was not persisted");

    clearPendingRefundOperation("sale-1", crypto.randomUUID());
    expect(loadPendingRefundOperation("sale-1")).toEqual(operation);

    clearPendingRefundOperation("sale-1", operation.operationId);
    expect(loadPendingRefundOperation("sale-1")).toBeNull();
  });

  it("persists a recovered server attempt without replacing a valid local operation", () => {
    const recovered = saveRecoveredPendingRefundOperation("sale-1", SERVER_ATTEMPT);

    expect(recovered).toMatchObject({
      refundAttemptOperationId: SERVER_ATTEMPT.operation_id,
      refundAttemptId: SERVER_ATTEMPT.id,
      parentSaleId: SERVER_ATTEMPT.parent_sale_id,
      items: SERVER_ATTEMPT.items,
    });
    expect(recovered?.operationId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );

    const second = saveRecoveredPendingRefundOperation("sale-1", {
      ...SERVER_ATTEMPT,
      id: "attempt-2",
    });
    expect(second).toEqual(recovered);
    expect(loadPendingRefundOperation("sale-1")).toEqual(recovered);
  });

  it("rejects corrupt markers and unavailable storage", () => {
    window.localStorage.setItem(
      pendingRefundOperationKey("sale-1"),
      JSON.stringify({ operationId: "not-a-uuid", parentSaleId: "sale-1" }),
    );
    expect(loadPendingRefundOperation("sale-1")).toBeNull();
    window.localStorage.setItem(
      pendingRefundOperationKey("sale-1"),
      JSON.stringify({
        operationId: crypto.randomUUID(),
        parentSaleId: "sale-1",
        items: [
          { sale_item_id: "item-1", qty: "1" },
          { sale_item_id: "item-1", qty: "2" },
        ],
        refundAttemptOperationId: null,
        refundAttemptId: null,
      }),
    );
    expect(loadPendingRefundOperation("sale-1")).toBeNull();

    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    expect(
      createPendingRefundOperation("sale-2", [{ sale_item_id: "item-2", qty: "1" }], false),
    ).toBeNull();
    setItem.mockRestore();
  });
});
