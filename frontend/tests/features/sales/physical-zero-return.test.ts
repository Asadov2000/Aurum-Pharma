import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  parsePhysicalZeroReturnStatus,
  physicalZeroReturnRequestSchema,
  type PhysicalRefundOptions,
  type PhysicalRefundQuote,
  type PhysicalZeroReturnInput,
} from "@/features/sales/physicalRefund";
import {
  acknowledgePhysicalRefund,
  inspectPhysicalRefund,
  isZeroPhysicalReturn,
  loadPhysicalRefund,
  physicalRefundKey,
  resetRejectedPhysicalRefund,
  runPhysicalRefund,
  runPhysicalZeroReturn,
} from "@/features/sales/physicalRefundRecovery";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), generation: 0 }));
vi.mock("@/lib/api", () => ({ api: { get: mocks.get, post: mocks.post } }));
vi.mock("@/features/pos/posStorageScope", async (original) => ({
  ...(await original<typeof import("@/features/pos/posStorageScope")>()),
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
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
const input: PhysicalZeroReturnInput = {
  items: [{ line_id: uuid(7), sale_format: "blister", quantity: 2 }],
  expected_revision_hash: "a".repeat(64),
  reason_code: "quality_issue",
  comment: "Повреждена упаковка",
};
const options: PhysicalRefundOptions = {
  parent_sale_id: scope.parentSaleId,
  branch_id: scope.branchId,
  register_id: scope.registerId,
  original_receipt_number: "R-1",
  reason_mode: "optional",
  lines: [
    {
      line_id: uuid(7),
      name: "Лекарство",
      unit_label: "табл.",
      packaging: {
        version: 2,
        unit: "tablet",
        units_per_pack: 30,
        units_per_blister: 10,
        dispense_step: 1,
      },
      sold_base_units: 30,
      returned_base_units: 20,
      remaining_base_units: 10,
      original_sale_format: "pack",
      original_retail_unit_price: "60.00",
    },
  ],
};
const quote: PhysicalRefundQuote = {
  parent_sale_id: scope.parentSaleId,
  revision_hash: input.expected_revision_hash,
  total_amount: "0.00",
  currency: "TJS",
  lines: [
    {
      ...input.items[0]!,
      base_units: 20,
      offset: 0,
      gross_amount: "40.00",
      discount_amount: "40.00",
      net_amount: "0.00",
    },
  ],
};
function pending() {
  const value = loadPhysicalRefund(scope);
  if (!value || !isZeroPhysicalReturn(value)) throw new Error("Expected zero intent");
  return value;
}
function prepared(operationId = pending().request.operation_id) {
  return {
    version: 1 as const,
    kind: "physical_zero_return" as const,
    operation_id: operationId,
    parent_sale_id: scope.parentSaleId,
    tenant_id: scope.tenantId,
    branch_id: scope.branchId,
    actor_id: scope.userId,
    state: "prepared_quarantine" as const,
    currency: "TJS" as const,
    total_amount: "0.00" as const,
    lines: [{ line_id: uuid(7), base_units: 20, offset: 0 }],
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
describe("physical zero return durable recovery", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    localStorage.clear();
    mocks.generation = 0;
    serialLocks();
    mocks.get.mockImplementation((url: string) =>
      Promise.resolve({
        data: url.endsWith("/options")
          ? options
          : {
              state: "not_found",
              operation_id: pending().request.operation_id,
              parent_sale_id: scope.parentSaleId,
            },
      }),
    );
    mocks.post.mockImplementation(() => Promise.resolve({ data: prepared() }));
  });
  it("persists the exact no-money request before GET/POST and sends no local discriminator", async () => {
    mocks.post.mockImplementationOnce((url: string, request: unknown) => {
      expect(pending().request).toEqual(request);
      expect(request).toEqual({ ...input, operation_id: pending().request.operation_id });
      expect(url).toBe(`/sales/${scope.parentSaleId}/physical-zero-returns/complete`);
      return Promise.resolve({ data: prepared() });
    });
    expect((await runPhysicalZeroReturn(scope, input, quote)).state).toBe("prepared_quarantine");
    expect(pending().kind).toBe("physical_zero_return");
    expect(mocks.get).toHaveBeenCalledWith(
      `/sales/${scope.parentSaleId}/physical-zero-returns/operations/${pending().request.operation_id}`,
      { timeout: 8_000 },
    );
  });
  it("reload only GETs after a lost POST response and retains the operation identifier", async () => {
    mocks.post.mockRejectedValueOnce(new Error("Response lost"));
    await expect(runPhysicalZeroReturn(scope, input, quote)).rejects.toThrow("Response lost");
    const saved = pending().request;
    mocks.get.mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/options") ? options : prepared() }),
    );
    expect((await inspectPhysicalRefund(scope))?.state).toBe("prepared_quarantine");
    expect(pending().request).toEqual(saved);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(await acknowledgePhysicalRefund(scope, saved.operation_id)).toBe(true);
    expect(loadPhysicalRefund(scope)).toBeNull();
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("unknown status cannot be cleared and explicit retry sends the same request", async () => {
    mocks.post.mockRejectedValueOnce(new Error("Offline"));
    await expect(runPhysicalZeroReturn(scope, input)).rejects.toThrow("Offline");
    const saved = pending().request;
    expect((await inspectPhysicalRefund(scope))?.state).toBe("not_found");
    expect(await acknowledgePhysicalRefund(scope, saved.operation_id)).toBe(false);
    await expect(resetRejectedPhysicalRefund(scope)).rejects.toThrow("неизвестен");
    await runPhysicalZeroReturn(scope);
    expect(mocks.post).toHaveBeenLastCalledWith(
      `/sales/${scope.parentSaleId}/physical-zero-returns/complete`,
      saved,
      { timeout: 15_000 },
    );
  });
  it("cash and zero cannot overlap in the same durable queue and browser lock", async () => {
    mocks.post.mockRejectedValueOnce(new Error("Offline"));
    await expect(runPhysicalZeroReturn(scope, input)).rejects.toThrow("Offline");
    await expect(
      runPhysicalRefund(scope, {
        ...input,
        shift_id: uuid(6),
        expected_total_amount: "1.00",
      }),
    ).rejects.toThrow("незавершённый");
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(physicalRefundKey(scope))).not.toBeNull();
  });
  it("serializes two zero confirmations into one immutable operation and one POST", async () => {
    let committed = false;
    mocks.get.mockImplementation((url: string) =>
      Promise.resolve({
        data: url.endsWith("/options")
          ? options
          : committed
            ? prepared()
            : {
                state: "not_found",
                operation_id: pending().request.operation_id,
                parent_sale_id: scope.parentSaleId,
              },
      }),
    );
    mocks.post.mockImplementation(() => {
      committed = true;
      return Promise.resolve({ data: prepared() });
    });
    const results = await Promise.all([
      runPhysicalZeroReturn(scope, input, quote),
      runPhysicalZeroReturn(scope, input, quote),
    ]);
    expect(results[0]).toEqual(results[1]);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("retains a malformed acknowledgment and does not permit clearing", async () => {
    mocks.post.mockResolvedValueOnce({ data: { ...prepared(uuid(10)), receipt: {} } });
    await expect(runPhysicalZeroReturn(scope, input)).rejects.toThrow();
    expect(pending().definiteRejected).toBe(false);
    await expect(resetRejectedPhysicalRefund(scope)).rejects.toThrow("неизвестен");
  });
  it("fails before any API call when persistence is unavailable", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Quota");
    });
    await expect(runPhysicalZeroReturn(scope, input)).rejects.toThrow("не отправлена");
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("stops when the account changes during status lookup", async () => {
    mocks.get.mockImplementationOnce(() => {
      mocks.generation += 1;
      return Promise.resolve({
        data: {
          state: "not_found",
          operation_id: pending().request.operation_id,
          parent_sale_id: scope.parentSaleId,
        },
      });
    });
    await expect(runPhysicalZeroReturn(scope, input)).rejects.toThrow("Аккаунт изменился");
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it.each(["shift_id", "expected_total_amount", "kind"])(
    "rejects unexpected %s in zero requests",
    (field) => {
      expect(
        physicalZeroReturnRequestSchema.safeParse({
          ...input,
          operation_id: uuid(10),
          [field]: "0.00",
        }).success,
      ).toBe(false);
    },
  );
});

describe("zero acknowledgment scope and physical quantities", () => {
  const request = { ...input, operation_id: uuid(10) };
  it("accepts an immutable acknowledgment using server packaging and the reviewed quote", () => {
    const value = prepared(request.operation_id);
    expect(
      parsePhysicalZeroReturnStatus(value, scope.parentSaleId, request, scope, options, quote),
    ).toEqual(value);
  });
  it.each(["tenant_id", "actor_id", "branch_id", "parent_sale_id", "operation_id"])(
    "rejects another %s",
    (field) => {
      expect(() =>
        parsePhysicalZeroReturnStatus(
          { ...prepared(request.operation_id), [field]: uuid(99) },
          scope.parentSaleId,
          request,
          scope,
          options,
        ),
      ).toThrow();
    },
  );
  it.each(["quantity", "offset", "duplicate", "receipt", "amount"])(
    "rejects invalid %s without trusting persisted raw counts",
    (field) => {
      const value = prepared(request.operation_id);
      if (field === "quantity") value.lines[0]!.base_units = 2;
      if (field === "offset") value.lines[0]!.offset = 1;
      if (field === "duplicate") value.lines.push(value.lines[0]!);
      const payload: unknown =
        field === "receipt"
          ? { ...value, receipt: {} }
          : field === "amount"
            ? { ...value, total_amount: "1.00" }
            : value;
      expect(() =>
        parsePhysicalZeroReturnStatus(payload, scope.parentSaleId, request, scope, options, quote),
      ).toThrow();
    },
  );
  it("requires fresh server options for a prepared result", () => {
    expect(() =>
      parsePhysicalZeroReturnStatus(
        prepared(request.operation_id),
        scope.parentSaleId,
        request,
        scope,
      ),
    ).toThrow();
  });
});
