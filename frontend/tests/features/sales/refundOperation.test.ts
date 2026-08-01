import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingRefundOperation,
  createPendingRefundOperation,
  loadPendingRefundOperation,
  pendingRefundOperationKey,
} from "@/features/sales/refundOperation";

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
    expect(
      createPendingRefundOperation(
        "sale-1",
        [{ sale_item_id: "different", qty: "99" }],
        false,
      ),
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
        externalRefundConfirmed: false,
      }),
    );
    expect(loadPendingRefundOperation("sale-1")).toBeNull();

    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable");
    });
    expect(
      createPendingRefundOperation(
        "sale-2",
        [{ sale_item_id: "item-2", qty: "1" }],
        false,
      ),
    ).toBeNull();
    setItem.mockRestore();
  });
});
