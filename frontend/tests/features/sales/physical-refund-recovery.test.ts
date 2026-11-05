import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  parsePhysicalRefundStatus,
  type PhysicalRefundInput,
} from "@/features/sales/physicalRefund";
import {
  acknowledgePhysicalRefund,
  inspectPhysicalRefund,
  isZeroPhysicalReturn,
  loadPhysicalRefund,
  physicalRefundKey,
  runPhysicalRefund,
  resetRejectedPhysicalRefund,
} from "@/features/sales/physicalRefundRecovery";

const mocks = vi.hoisted(() => ({ generation: 0, status: vi.fn(), complete: vi.fn() }));
vi.mock("@/features/pos/posStorageScope", async (original) => ({
  ...(await original<typeof import("@/features/pos/posStorageScope")>()),
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
}));
vi.mock("@/features/sales/physicalRefund", async (original) => ({
  ...(await original<typeof import("@/features/sales/physicalRefund")>()),
  getPhysicalRefundStatus: mocks.status,
  completePhysicalRefund: mocks.complete,
}));
const uuid = (value: number): string =>
  `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const scope = {
  tenantId: uuid(1),
  userId: uuid(2),
  parentSaleId: uuid(3),
  branchId: uuid(4),
  registerId: uuid(5),
};
const input: PhysicalRefundInput = {
  shift_id: uuid(6),
  items: [{ line_id: uuid(7), sale_format: "unit", quantity: 2 }],
  expected_total_amount: "3.99",
  expected_revision_hash: "a".repeat(64),
  reason_code: "quality_issue",
  comment: "Повреждена упаковка",
};
function pending() {
  const value = loadPhysicalRefund(scope);
  if (!value) throw new Error("Missing fixture intent");
  if (isZeroPhysicalReturn(value)) throw new Error("Expected cash fixture intent");
  return value;
}
function completed() {
  return {
    state: "completed" as const,
    operation_id: pending().request.operation_id,
    parent_sale_id: scope.parentSaleId,
    sale_id: uuid(8),
    total_amount: "3.99",
    receipt: {
      schema_version: 3 as const,
      document_kind: "physical_cash_refund_receipt" as const,
      parent_sale_id: scope.parentSaleId,
      parent_receipt_number: "R-1",
      receipt_number: "R-2",
      receipt_sequence: 2,
      completed_at: "2026-10-09T08:30:00Z",
      is_test: false,
      lines: [
        {
          original_line_id: uuid(7),
          position: 1,
          name: "Saved medicine",
          packaging: {
            version: 2 as const,
            unit: "tablet" as const,
            units_per_pack: 30,
            units_per_blister: 10,
            dispense_step: 1,
          },
          original_sale_format: "pack" as const,
          original_retail_unit_price: "60.00",
          sale_format: "unit" as const,
          quantity: 2,
          base_units: 2,
          offset: 0,
          gross_amount: "4.00",
          discount_amount: "0.01",
          net_amount: "3.99",
        },
      ],
      payment_method: "cash" as const,
      total_amount: "3.99",
      currency: "TJS" as const,
    },
  };
}
function serialLocks(): void {
  let tail: Promise<unknown> = Promise.resolve();
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: (_key: string, execute: () => Promise<unknown>) => {
        const operation = tail.then(execute);
        tail = operation.catch(() => undefined);
        return operation;
      },
    },
  });
}
function rejectedResponse(status: number, code: string): Error {
  return Object.assign(new Error("Rejected"), {
    isAxiosError: true,
    response: { status, data: { error: { code, message: "Rejected" } } },
  });
}
describe("durable physical cash refund protocol", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    localStorage.clear();
    mocks.generation = 0;
    serialLocks();
    mocks.status.mockImplementation(() =>
      Promise.resolve({
        state: "not_found",
        operation_id: pending().request.operation_id,
        parent_sale_id: scope.parentSaleId,
      }),
    );
    mocks.complete.mockImplementation(() => {
      const result = completed();
      mocks.status.mockResolvedValue(result);
      return Promise.resolve(result);
    });
  });
  it("stores exact reason, revision and quantities before the first request and retains completed intent", async () => {
    mocks.status.mockImplementationOnce(() => {
      expect(pending().request).toMatchObject(input);
      return Promise.resolve({ state: "not_found" });
    });
    const result = await runPhysicalRefund(scope, input);
    expect(result).toEqual(completed());
    expect(mocks.complete).toHaveBeenCalledWith(scope.parentSaleId, pending().request, scope);
    expect(loadPhysicalRefund(scope)).not.toBeNull();
  });
  it("reload inspection only GETs and never repeats a payout after a lost response", async () => {
    mocks.complete.mockRejectedValueOnce(new Error("Response lost"));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Response lost");
    const operationId = pending().request.operation_id;
    mocks.status.mockResolvedValue(completed());
    expect((await inspectPhysicalRefund(scope))?.state).toBe("completed");
    expect(pending().request.operation_id).toBe(operationId);
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });
  it("not_found after reload retains the exact request until an explicit retry", async () => {
    mocks.complete.mockRejectedValueOnce(new Error("Offline"));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Offline");
    const request = pending().request;
    expect((await inspectPhysicalRefund(scope))?.state).toBe("not_found");
    expect(mocks.complete).toHaveBeenCalledTimes(1);
    await runPhysicalRefund(scope);
    expect(mocks.complete).toHaveBeenLastCalledWith(scope.parentSaleId, request, scope);
  });
  it("cannot edit reason or quote revision while a persisted operation is unresolved", async () => {
    mocks.complete.mockRejectedValueOnce(new Error("Offline"));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Offline");
    await expect(runPhysicalRefund(scope, { ...input, reason_code: "other" })).rejects.toThrow(
      "незавершённый",
    );
    await expect(
      runPhysicalRefund(scope, { ...input, expected_revision_hash: "b".repeat(64) }),
    ).rejects.toThrow("незавершённый");
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });
  it("refuses submission when persistence fails", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Quota exceeded");
    });
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("не отправлена");
    expect(mocks.status).not.toHaveBeenCalled();
    expect(mocks.complete).not.toHaveBeenCalled();
  });
  it("retains damaged intent and refuses both GET and POST", async () => {
    localStorage.setItem(physicalRefundKey(scope), "{broken");
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("повреждён");
    expect(localStorage.getItem(physicalRefundKey(scope))).toBe("{broken");
    expect(mocks.status).not.toHaveBeenCalled();
    expect(mocks.complete).not.toHaveBeenCalled();
  });
  it("stops after session generation changes during status lookup", async () => {
    mocks.status.mockImplementationOnce(() => {
      mocks.generation += 1;
      return Promise.resolve({ state: "not_found" });
    });
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Аккаунт изменился");
    expect(loadPhysicalRefund(scope)).not.toBeNull();
    expect(mocks.complete).not.toHaveBeenCalled();
  });
  it("serializes two simultaneous confirmations into one POST with one operation identifier", async () => {
    const results = await Promise.all([
      runPhysicalRefund(scope, input),
      runPhysicalRefund(scope, input),
    ]);
    expect(results[0]).toEqual(results[1]);
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });
  it("clears only matching completed intent after another verified GET", async () => {
    const result = await runPhysicalRefund(scope, input);
    expect(await acknowledgePhysicalRefund(scope, uuid(90))).toBe(false);
    expect(loadPhysicalRefund(scope)).not.toBeNull();
    expect(await acknowledgePhysicalRefund(scope, result.operation_id)).toBe(true);
    expect(loadPhysicalRefund(scope)).toBeNull();
  });
  it("cannot acknowledge an unknown result", async () => {
    mocks.complete.mockRejectedValueOnce(new Error("Offline"));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Offline");
    expect(await acknowledgePhysicalRefund(scope, pending().request.operation_id)).toBe(false);
    expect(loadPhysicalRefund(scope)).not.toBeNull();
  });
  it("fails closed without cross-tab browser locks", async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("современный браузер");
    expect(mocks.complete).not.toHaveBeenCalled();
  });
  it("separates queues by branch and user", async () => {
    await runPhysicalRefund(scope, input);
    expect(loadPhysicalRefund({ ...scope, userId: uuid(99) })).toBeNull();
    expect(loadPhysicalRefund({ ...scope, branchId: uuid(99) })).toBeNull();
  });
  it.each([
    [409, "conflict"],
    [422, "business_rule_violation"],
    [422, "validation_error"],
  ])("allows explicit reset after a verified %s rejection and not_found", async (status, code) => {
    mocks.complete.mockRejectedValueOnce(rejectedResponse(Number(status), String(code)));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Rejected");
    expect(pending().definiteRejected).toBe(true);
    expect(await resetRejectedPhysicalRefund(scope)).toEqual({ state: "cleared" });
    expect(loadPhysicalRefund(scope)).toBeNull();
  });
  it("a timeout or unknown 404 never permits clearing a not_found operation", async () => {
    mocks.complete.mockRejectedValueOnce(rejectedResponse(404, "not_found"));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Rejected");
    expect(pending().definiteRejected).toBe(false);
    await expect(resetRejectedPhysicalRefund(scope)).rejects.toThrow("неизвестен");
    expect(loadPhysicalRefund(scope)).not.toBeNull();
  });
  it("an unstructured proxy 409 does not mark the request definitively rejected", async () => {
    mocks.complete.mockRejectedValueOnce(
      Object.assign(new Error("Proxy"), {
        isAxiosError: true,
        response: { status: 409, data: "Conflict" },
      }),
    );
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Proxy");
    expect(pending().definiteRejected).toBe(false);
  });
  it("removes an old rejection flag before retrying, retaining unknown subsequent results", async () => {
    mocks.complete.mockRejectedValueOnce(rejectedResponse(409, "conflict"));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Rejected");
    mocks.complete.mockImplementationOnce(() => {
      expect(pending().definiteRejected).toBe(false);
      return Promise.reject(new Error("Response lost"));
    });
    await expect(runPhysicalRefund(scope)).rejects.toThrow("Response lost");
    await expect(resetRejectedPhysicalRefund(scope)).rejects.toThrow("неизвестен");
  });
  it("does not reset a completed operation after an earlier rejection", async () => {
    mocks.complete.mockRejectedValueOnce(rejectedResponse(409, "conflict"));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Rejected");
    mocks.status.mockResolvedValue(completed());
    expect((await resetRejectedPhysicalRefund(scope)).state).toBe("completed");
    expect(loadPhysicalRefund(scope)).not.toBeNull();
  });
});

describe("strict refund completion identity", () => {
  beforeEach(async () => {
    localStorage.clear();
    serialLocks();
    mocks.generation = 0;
    mocks.status.mockResolvedValue({ state: "not_found" });
    mocks.complete.mockRejectedValue(new Error("Offline"));
    await expect(runPhysicalRefund(scope, input)).rejects.toThrow("Offline");
  });
  it("accepts historical allocated amounts even when original price format differs", () => {
    expect(parsePhysicalRefundStatus(completed(), scope.parentSaleId, pending().request)).toEqual(
      completed(),
    );
  });
  it.each(["operation", "parent", "amount", "quantity", "cost", "discount"])(
    "rejects corrupted %s",
    (field) => {
      const value = completed();
      if (field === "operation") value.operation_id = uuid(90);
      if (field === "parent") value.receipt.parent_sale_id = uuid(90);
      if (field === "amount") value.total_amount = "4.00";
      if (field === "quantity") value.receipt.lines[0]!.quantity = 3;
      if (field === "discount") value.receipt.lines[0]!.discount_amount = "0.00";
      const payload: unknown = field === "cost" ? { ...value, total_cost_amount: "1.00" } : value;
      expect(() =>
        parsePhysicalRefundStatus(payload, scope.parentSaleId, pending().request),
      ).toThrow();
    },
  );
});
