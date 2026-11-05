import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import {
  trainingCheckoutRequestSchema,
  startTrainingCheckout,
  inspectTrainingCheckout,
  retryTrainingCheckout,
  acknowledgeTrainingCheckout,
  loadTrainingCheckout,
  parseTrainingResult,
  getTrainingQuote,
  type TrainingContext,
} from "@/features/pos/trainingCheckout";
import { trainingCheckoutKey } from "@/features/pos/trainingCheckoutPresence";
import { unitPaymentMarkerKey } from "@/features/pos/unitPaymentPreparationPresence";
import { physicalCashRecoveryKey } from "@/features/pos/physicalCashRecoveryPresence";
import { trainingFixture } from "./trainingCheckoutFixture";

const m = vi.hoisted(() => ({
  active: true,
  test: true,
  writable: true,
  permission: true,
  readonly: false,
  shift: vi.fn(),
  quote: vi.fn(),
}));
vi.mock("@/features/pos/api", () => ({ getCurrentShift: m.shift }));
vi.mock("@/features/pos/unitPaymentPreparation", () => ({ getUnitPaymentQuote: m.quote }));
vi.mock("@/features/pos/operationId", () => ({
  generateUuidV4: () => "00000000-0000-4000-8000-000000000007",
}));
vi.mock("@/features/pos/posStorageScope", async (original) => ({
  ...(await original<typeof import("@/features/pos/posStorageScope")>()),
  capturePosSessionGuard: () => () => m.active,
}));
vi.mock("@/features/auth/permissions", () => ({ hasPermissionForBranch: () => m.permission }));
vi.mock("@/stores/auth", () => ({
  useAuthStore: {
    getState: () => ({
      accessToken: "synthetic",
      user: { support_access: { is_read_only: m.readonly } },
    }),
  },
}));
vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  withAuthSession: (c: object) => c,
}));
function context(): TrainingContext {
  return {
    scope: trainingFixture().scope,
    active: () => m.active,
    isTest: () => m.test,
    writable: () => m.writable,
  };
}
function store(mixed = true) {
  const f = trainingFixture(mixed),
    p = { version: 1 as const, scope: f.scope, request: f.request };
  localStorage.setItem(trainingCheckoutKey(f.scope.saleId, f.scope), JSON.stringify(p));
  return p;
}
function serial() {
  let tail: Promise<unknown> = Promise.resolve();
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: (_key: string, fn: () => Promise<unknown>) => {
        const next = tail.then(fn);
        tail = next.catch(() => undefined);
        return next;
      },
    },
  });
}
beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.stubGlobal("crypto", webcrypto);
  localStorage.clear();
  m.active = true;
  m.test = true;
  m.writable = true;
  m.permission = true;
  m.readonly = false;
  serial();
  const f = trainingFixture();
  m.shift.mockResolvedValue({
    id: f.result.shift_id,
    status: "open",
    opened_by_user_id: f.scope.userId,
    branch_id: f.scope.branchId,
    register_id: f.scope.registerId,
    currency: "TJS",
  });
  m.quote.mockResolvedValue(f.quote);
  vi.mocked(api.post).mockResolvedValue({ data: f.result });
  vi.mocked(api.get).mockResolvedValue({
    data: {
      state: "completed",
      operation_id: f.request.operation_id,
      sale_id: f.scope.saleId,
      result: f.result,
    },
  });
});
afterEach(() => vi.unstubAllGlobals());
describe("training-only durable checkout", () => {
  it.each([false, true])("accepts exact physical/mixed TEST history (%s)", async (mixed) => {
    const f = trainingFixture(mixed),
      p = store(mixed);
    expect(await parseTrainingResult(f.result, p, () => undefined)).toEqual(f.result);
  });
  it("persists exact safe request before POST and uses only training route", async () => {
    const f = trainingFixture();
    const { operation_id: unused, ...input } = f.request;
    void unused;
    vi.mocked(api.post).mockImplementationOnce(async (path, body) => {
      expect(loadTrainingCheckout(f.scope)?.request).toEqual(body);
      expect(path).toBe(`/sales/${f.scope.saleId}/training/complete`);
      return { data: f.result };
    });
    expect(await startTrainingCheckout(context(), input)).toEqual(f.result);
    const raw = localStorage.getItem(trainingCheckoutKey(f.scope.saleId, f.scope))!;
    for (const field of ["accessToken", "receipt", "simulation", "payment_id", "stock_command_id"])
      expect(raw).not.toContain(field);
  });
  it("lost response and reload recover via GET without another POST", async () => {
    const f = trainingFixture(),
      { operation_id: unused, ...input } = f.request;
    void unused;
    vi.mocked(api.post).mockRejectedValueOnce(new Error("lost"));
    await expect(startTrainingCheckout(context(), input)).rejects.toThrow("lost");
    const pending = loadTrainingCheckout(f.scope);
    expect(pending).not.toBeNull();
    expect((await inspectTrainingCheckout(context()))?.state).toBe("completed");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(loadTrainingCheckout(f.scope)).toEqual(pending);
  });
  it("typed not_found permits only explicit retry with identical UUID/body", async () => {
    const f = trainingFixture(),
      p = store();
    vi.mocked(api.get).mockResolvedValue({
      data: { state: "not_found", operation_id: f.request.operation_id, sale_id: f.scope.saleId },
    });
    expect(await inspectTrainingCheckout(context())).toEqual({ state: "not_found" });
    expect(api.post).not.toHaveBeenCalled();
    await retryTrainingCheckout(context());
    expect(vi.mocked(api.post).mock.calls[0]?.[1]).toEqual(p.request);
  });
  it.each([403, 404, 409, 500])(
    "read error %s retains journal and prevents blind retry",
    async (status) => {
      const p = store();
      vi.mocked(api.get).mockRejectedValue({ response: { status } });
      await expect(retryTrainingCheckout(context())).rejects.toBeDefined();
      expect(loadTrainingCheckout(p.scope)).toEqual(p);
      expect(api.post).not.toHaveBeenCalled();
    },
  );
  it("readonly/closed shift permits GET, not new commands", async () => {
    store();
    m.readonly = true;
    m.writable = false;
    m.shift.mockResolvedValue(null);
    expect((await inspectTrainingCheckout(context()))?.state).toBe("completed");
    await expect(retryTrainingCheckout(context())).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
  });
  it.each(["active", "test", "permission"] as const)("fails closed on changed %s", async (flag) => {
    store();
    m[flag] = false;
    await expect(inspectTrainingCheckout(context())).rejects.toThrow();
    expect(api.get).not.toHaveBeenCalled();
  });
  it("snapshots caller scope and request before waiting for authority", async () => {
    const f = trainingFixture(),
      c = context(),
      { operation_id: unused, ...input } = f.request;
    void unused;
    const originalRequest = structuredClone(f.request);
    m.shift.mockImplementation(async () => {
      input.simulated_payments[0]!.amount = "0.31";
      c.scope.saleId = "00000000-0000-4000-8000-000000000099";
      return {
        id: f.result.shift_id,
        status: "open",
        opened_by_user_id: f.scope.userId,
        branch_id: f.scope.branchId,
        register_id: f.scope.registerId,
        currency: "TJS",
      };
    });
    await startTrainingCheckout(c, input);
    expect(vi.mocked(api.post).mock.calls[0]?.[0]).toBe(
      `/sales/${f.scope.saleId}/training/complete`,
    );
    expect(vi.mocked(api.post).mock.calls[0]?.[1]).toEqual(originalRequest);
  });
  it("rechecks opposite payment markers after shift await before POST", async () => {
    const f = trainingFixture(),
      { operation_id: unused, ...input } = f.request;
    void unused;
    m.shift.mockImplementation(async () => {
      localStorage.setItem(unitPaymentMarkerKey(f.scope.saleId, f.scope), "{}");
      return { id: f.result.shift_id };
    });
    await expect(startTrainingCheckout(context(), input)).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
  });
  it("opposite marker during recovery blocks result acceptance and retains journal", async () => {
    const f = trainingFixture(),
      p = store();
    vi.mocked(api.get).mockImplementationOnce(async () => {
      localStorage.setItem(unitPaymentMarkerKey(f.scope.saleId, f.scope), "{}");
      return {
        data: {
          state: "completed",
          operation_id: f.request.operation_id,
          sale_id: f.scope.saleId,
          result: f.result,
        },
      };
    });
    await expect(inspectTrainingCheckout(context())).rejects.toThrow();
    expect(loadTrainingCheckout(f.scope)).toEqual(p);
    expect(api.post).not.toHaveBeenCalled();
  });
  it("rechecks context after own-shift await", async () => {
    const f = trainingFixture(),
      { operation_id: unused, ...input } = f.request;
    void unused;
    m.shift.mockImplementation(async () => {
      m.active = false;
      return { id: f.result.shift_id };
    });
    await expect(startTrainingCheckout(context(), input)).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
  });
  it("rejects changed context during quote digest/result validation", async () => {
    const p = store();
    let checks = 0;
    await expect(
      parseTrainingResult(trainingFixture().result, p, () => {
        checks++;
        throw new Error("changed");
      }),
    ).rejects.toThrow("changed");
    expect(checks).toBe(1);
  });
  it.each(["storage", "locks", "realMarker", "corrupt"])(
    "blocks unsafe %s without HTTP",
    async (kind) => {
      const f = trainingFixture();
      if (kind === "storage")
        vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
          throw new Error("denied");
        });
      if (kind === "locks")
        Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
      if (kind === "realMarker")
        localStorage.setItem(physicalCashRecoveryKey(f.scope.saleId, f.scope), "{}");
      if (kind === "corrupt")
        localStorage.setItem(trainingCheckoutKey(f.scope.saleId, f.scope), "{bad");
      await expect(inspectTrainingCheckout(context())).rejects.toThrow();
      expect(api.get).not.toHaveBeenCalled();
      expect(api.post).not.toHaveBeenCalled();
    },
  );
  it("write failure prevents command; no UUID replacement when marker exists", async () => {
    const f = trainingFixture(),
      { operation_id: unused, ...input } = f.request;
    void unused;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    await expect(startTrainingCheckout(context(), input)).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
  });
  it("serializes two tabs and submits one durable command", async () => {
    const f = trainingFixture(),
      { operation_id: unused, ...input } = f.request;
    void unused;
    const r = await Promise.allSettled([
      startTrainingCheckout(context(), input),
      startTrainingCheckout(context(), input),
    ]);
    expect(r.filter((x) => x.status === "fulfilled")).toHaveLength(1);
    expect(api.post).toHaveBeenCalledTimes(1);
  });
  it("clears only after explicit verified completed GET", async () => {
    const f = trainingFixture();
    store();
    vi.mocked(api.get).mockResolvedValueOnce({
      data: { state: "not_found", operation_id: f.request.operation_id, sale_id: f.scope.saleId },
    });
    await expect(acknowledgeTrainingCheckout(context())).rejects.toThrow();
    expect(loadTrainingCheckout(f.scope)).not.toBeNull();
    await acknowledgeTrainingCheckout(context());
    expect(loadTrainingCheckout(f.scope)).toBeNull();
  });
  it.each(["test", "kind", "header", "cost", "total", "row", "payment", "revision", "number"])(
    "rejects corrupted result %s",
    async (kind) => {
      const f = trainingFixture(),
        p = store();
      if (kind === "test") Reflect.set(f.result, "is_test", false);
      if (kind === "kind") Reflect.set(f.result, "document_kind", "mixed_checkout");
      if (kind === "header") f.result.receipt.tenant_id = "00000000-0000-4000-8000-000000000099";
      if (kind === "cost") Reflect.set(f.result, "total_cost_amount", "1.00");
      if (kind === "total") f.result.total_amount = "0.33";
      if (kind === "row") f.result.receipt.lines.reverse();
      if (kind === "payment") f.result.receipt.simulated_payments[0]!.amount = "0.31";
      if (kind === "revision") f.result.request.expected_revision_hash = "b".repeat(64);
      if (kind === "number") f.result.receipt_number = "REAL-1";
      await expect(parseTrainingResult(f.result, p, () => undefined)).rejects.toThrow();
    },
  );
  it.each(["format", "label", "missing-format", "missing-label", "extra"])(
    "rejects invalid legacy TEST receipt display contract %s",
    async (kind) => {
      const f = trainingFixture(),
        p = store(),
        line = f.result.receipt.lines.find((row) => row.inventory_kind === "legacy_pack");
      expect(line).toBeDefined();
      if (!line) throw new Error("Missing mixed fixture row");
      if (kind === "format") Reflect.set(line, "sale_format", "unit");
      if (kind === "label") Reflect.set(line, "unit_label", "табл.");
      if (kind === "missing-format") Reflect.deleteProperty(line, "sale_format");
      if (kind === "missing-label") Reflect.deleteProperty(line, "unit_label");
      if (kind === "extra") Reflect.set(line, "unexpected", true);
      await expect(parseTrainingResult(f.result, p, () => undefined)).rejects.toThrow();
    },
  );
  it.each([1, 1.1, "1e0", "01.00", "0.001"])("rejects noncanonical money %s", (amount) => {
    const r = trainingFixture().request;
    expect(trainingCheckoutRequestSchema.safeParse({ ...r, cash_tendered: amount }).success).toBe(
      false,
    );
  });
  it("validates split allocations exactly and calculates change from cash portion", async () => {
    const f = trainingFixture(),
      p = store();
    f.request.simulated_payments = [
      { method: "cash", amount: "0.10" },
      { method: "card", amount: "0.22" },
    ];
    f.result.receipt.simulated_payments = f.request.simulated_payments;
    f.result.receipt.change_amount = "0.90";
    p.request = f.request;
    expect(await parseTrainingResult(f.result, p, () => undefined)).toEqual(f.result);
  });
  it("quotes only for an uncommitted test draft and binds original scope", async () => {
    expect(await getTrainingQuote(context())).toEqual(trainingFixture().quote);
    store();
    await expect(getTrainingQuote(context())).rejects.toThrow();
  });
});
