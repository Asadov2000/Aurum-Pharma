import { webcrypto } from "node:crypto";
import { TextEncoder } from "node:util";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  physicalDispositionRequestHash,
  physicalDispositionTarget,
  type PhysicalDispositionInput,
  type PhysicalDispositionReviewedTarget,
} from "@/features/customerReturns/physicalDisposition";
import {
  acknowledgePhysicalDisposition,
  inspectPhysicalDisposition,
  loadPhysicalDisposition,
  physicalDispositionKey,
  runPhysicalDisposition,
} from "@/features/customerReturns/physicalDispositionRecovery";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), generation: 0 }));
vi.mock("@/lib/api", () => ({ api: { get: mocks.get, post: mocks.post } }));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
}));
const uuid = (value: number): string =>
  `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const scope = { tenantId: uuid(1), userId: uuid(2), branchId: uuid(3) };
const reviewed: PhysicalDispositionReviewedTarget = {
  inventory_kind: "physical_unit",
  intake_operation_id: uuid(4),
  line_id: uuid(5),
  lot_id: uuid(6),
  base_units: 5,
  unit_label: "табл.",
};
const input: PhysicalDispositionInput = {
  target: physicalDispositionTarget(reviewed),
  disposition_type: "disposed",
  reason_code: "damaged",
  comment: "Претензия",
};
function pending() {
  const stored = loadPhysicalDisposition(scope);
  if (!stored) throw new Error("Missing intent");
  return stored;
}
async function resolved() {
  const saved = pending();
  return {
    version: 1 as const,
    document_kind: "physical_customer_return_disposition" as const,
    state: "resolved" as const,
    decision_operation_id: saved.request.decision_operation_id,
    tenant_id: scope.tenantId,
    branch_id: scope.branchId,
    parent_sale_id: uuid(7),
    intake_kind: "cash_refund" as const,
    target: saved.reviewedTarget,
    disposition_type: saved.request.disposition_type,
    reason_code: saved.request.reason_code,
    comment: saved.request.comment,
    request_hash: await physicalDispositionRequestHash(saved.request, scope.tenantId),
    actor_id: uuid(8),
    resolved_at: "2026-10-12T10:00:00Z",
  };
}
function serialLocks() {
  let tail = Promise.resolve<unknown>(undefined);
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: vi.fn((_key: string, callback: () => Promise<unknown>) => {
        const task = tail.then(callback);
        tail = task.catch(() => undefined);
        return task;
      }),
    },
  });
}
beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  localStorage.clear();
  mocks.generation = 0;
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("TextEncoder", TextEncoder);
  serialLocks();
  mocks.get.mockImplementation(async () => ({
    data: {
      state: "not_found",
      decision_operation_id: pending().request.decision_operation_id,
      branch_id: pending().branchId,
    },
  }));
  mocks.post.mockImplementation(async () => ({ data: await resolved() }));
});
describe("durable physical disposition recovery", () => {
  it("recovers a physical whole-unit disposition with its historical unit intact", async () => {
    const physical: PhysicalDispositionReviewedTarget = {
      inventory_kind: "physical_unit",
      intake_operation_id: uuid(4),
      line_id: uuid(5),
      lot_id: uuid(6),
      base_units: 3,
      unit_label: "амп.",
    };
    const physicalInput = { ...input, target: physicalDispositionTarget(physical) };
    mocks.post.mockRejectedValueOnce(new Error("lost"));
    await expect(runPhysicalDisposition(scope, physicalInput, physical)).rejects.toThrow("lost");
    expect(pending().reviewedTarget).toEqual(physical);
    const result = await runPhysicalDisposition(scope);
    expect(result.target).toEqual(physical);
    expect(mocks.post.mock.calls[0]?.[1]).toEqual(mocks.post.mock.calls[1]?.[1]);
    mocks.get.mockResolvedValue({ data: result });
    expect(await acknowledgePhysicalDisposition(scope, result)).toBe(true);
  });
  it("persists only minimal reviewed facts and request before first HTTP call", async () => {
    mocks.get.mockImplementationOnce(async () => {
      const saved = pending();
      expect(saved).toEqual({
        version: 1,
        ...scope,
        request: expect.objectContaining(input),
        reviewedTarget: reviewed,
      });
      expect(saved.request.decision_operation_id).toMatch(/^[0-9a-f-]{36}$/);
      return {
        data: {
          state: "not_found",
          decision_operation_id: saved.request.decision_operation_id,
          branch_id: scope.branchId,
        },
      };
    });
    const result = await runPhysicalDisposition(scope, input, reviewed);
    expect(result.state).toBe("resolved");
    expect(pending().request).toEqual(mocks.post.mock.calls[0]?.[1]);
    expect(Object.keys(pending()).sort()).toEqual([
      "branchId",
      "request",
      "reviewedTarget",
      "tenantId",
      "userId",
      "version",
    ]);
    expect(localStorage.getItem(physicalDispositionKey(scope))).not.toContain("parent_sale_id");
  });
  it("opening never POSTs, including stored intent and not_found", async () => {
    expect(await inspectPhysicalDisposition(scope)).toBeNull();
    expect(mocks.get).not.toHaveBeenCalled();
    await runPhysicalDisposition(scope, input, reviewed);
    mocks.post.mockClear();
    expect((await inspectPhysicalDisposition(scope))?.state).toBe("not_found");
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("retains exact UUID and request after ambiguous POST loss", async () => {
    mocks.post.mockRejectedValueOnce(new Error("network lost"));
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow("network lost");
    const original = pending().request;
    const result = await runPhysicalDisposition(scope);
    expect(result.decision_operation_id).toBe(original.decision_operation_id);
    expect(mocks.post.mock.calls.map((call) => call[1])).toEqual([original, original]);
  });
  it("finds committed lost response by GET without another POST", async () => {
    mocks.post.mockRejectedValueOnce(new Error("lost"));
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow("lost");
    const result = await resolved();
    mocks.get.mockResolvedValue({ data: result });
    expect(await runPhysicalDisposition(scope)).toEqual(result);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("does not POST if status lookup fails or is malformed", async () => {
    mocks.get.mockRejectedValueOnce(new Error("offline"));
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow("offline");
    expect(pending()).toBeTruthy();
    mocks.get.mockResolvedValueOnce({ data: { state: "unknown" } });
    await expect(runPhysicalDisposition(scope)).rejects.toThrow();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("conflict preserves barrier and never rotates decision key", async () => {
    mocks.post.mockRejectedValue({
      response: { status: 409, data: { error: { code: "conflict" } } },
    });
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toBeDefined();
    const original = pending();
    await expect(
      runPhysicalDisposition(scope, { ...input, reason_code: "other" }, reviewed),
    ).rejects.toThrow("незавершённое");
    expect(pending()).toEqual(original);
    await expect(runPhysicalDisposition(scope)).rejects.toBeDefined();
    expect(mocks.post.mock.calls.map((call) => call[1])).toEqual([
      original.request,
      original.request,
    ]);
  });
  it("one tenant/user key restores a pending branch and blocks another row or branch", async () => {
    await runPhysicalDisposition(scope, input, reviewed);
    const original = pending();
    expect(loadPhysicalDisposition({ tenantId: scope.tenantId, userId: scope.userId })).toEqual(
      original,
    );
    const otherBranch = { ...scope, branchId: uuid(90) };
    expect(physicalDispositionKey(otherBranch)).toBe(physicalDispositionKey(scope));
    await expect(runPhysicalDisposition(otherBranch, input, reviewed)).rejects.toThrow(
      "другой точке",
    );
    await expect(inspectPhysicalDisposition(otherBranch)).rejects.toThrow("другой точке");
    const otherRow = { ...reviewed, line_id: uuid(91) };
    await expect(
      runPhysicalDisposition(
        scope,
        { ...input, target: physicalDispositionTarget(otherRow) },
        otherRow,
      ),
    ).rejects.toThrow("незавершённое");
    await expect(
      runPhysicalDisposition(scope, input, { ...reviewed, base_units: 6 }),
    ).rejects.toThrow("Количество");
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("different tenants and users never read another saved intent", async () => {
    await runPhysicalDisposition(scope, input, reviewed);
    expect(loadPhysicalDisposition({ ...scope, tenantId: uuid(90) })).toBeNull();
    expect(loadPhysicalDisposition({ ...scope, userId: uuid(90) })).toBeNull();
  });
  it.each(["not json", "{}", '{"version":99}'])(
    "corrupt storage blocks all I/O: %s",
    async (raw) => {
      localStorage.setItem(physicalDispositionKey(scope), raw);
      await expect(inspectPhysicalDisposition(scope)).rejects.toThrow("повреждено");
      await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow("повреждено");
      expect(localStorage.getItem(physicalDispositionKey(scope))).toBe(raw);
      expect(mocks.get).not.toHaveBeenCalled();
      expect(mocks.post).not.toHaveBeenCalled();
    },
  );
  it("rejects forged success, foreign stored scope and mismatched snapshot identity", async () => {
    await runPhysicalDisposition(scope, input, reviewed);
    const saved = pending();
    mocks.get.mockClear();
    for (const changed of [
      { ...saved, state: "resolved" },
      { ...saved, tenantId: uuid(90) },
      { ...saved, reviewedTarget: { ...reviewed, lot_id: uuid(90) } },
    ]) {
      localStorage.setItem(physicalDispositionKey(scope), JSON.stringify(changed));
      await expect(inspectPhysicalDisposition(scope)).rejects.toThrow("повреждено");
    }
    expect(mocks.get).not.toHaveBeenCalled();
  });
  it("new intent requires whole reviewed quantity before HTTP or persistence", async () => {
    await expect(runPhysicalDisposition(scope, input)).rejects.toThrow("всю строку");
    await expect(
      runPhysicalDisposition(scope, input, { ...reviewed, base_units: 0 }),
    ).rejects.toThrow();
    expect(loadPhysicalDisposition(scope)).toBeNull();
    expect(mocks.get).not.toHaveBeenCalled();
  });
  it("requires WebLocks and fails closed on storage read/write failures", async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow(
      "современный браузер",
    );
    serialLocks();
    const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow("прочитать");
    read.mockRestore();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow("сохранить");
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("verifies persistence instead of trusting a silent write", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow("сохранить");
    expect(mocks.get).not.toHaveBeenCalled();
  });
  it("session change during lookup prevents POST and leaves the durable request", async () => {
    mocks.get.mockImplementationOnce(async () => {
      const data = {
        state: "not_found",
        decision_operation_id: pending().request.decision_operation_id,
        branch_id: scope.branchId,
      };
      mocks.generation += 1;
      return { data };
    });
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow(
      "Аккаунт изменился",
    );
    expect(pending()).toBeTruthy();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("rechecks session after waiting for the per-user WebLock", async () => {
    let release: (() => Promise<unknown>) | undefined;
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: (_key: string, callback: () => Promise<unknown>) =>
          new Promise((resolve, reject) => {
            release = () => callback().then(resolve, reject);
          }),
      },
    });
    const running = runPhysicalDisposition(scope, input, reviewed);
    const failure = expect(running).rejects.toThrow("Аккаунт изменился");
    mocks.generation += 1;
    await release?.();
    await failure;
    expect(loadPhysicalDisposition(scope)).toBeNull();
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("session change during POST cannot acknowledge a stale response", async () => {
    mocks.post.mockImplementationOnce(async () => {
      const data = await resolved();
      mocks.generation += 1;
      return { data };
    });
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow(
      "Аккаунт изменился",
    );
    expect(pending()).toBeTruthy();
  });
  it("detects cross-tab storage replacement during a status request", async () => {
    mocks.get.mockImplementationOnce(async () => {
      const saved = pending();
      localStorage.setItem(
        physicalDispositionKey(scope),
        JSON.stringify({ ...saved, request: { ...saved.request, comment: "Изменено" } }),
      );
      return {
        data: {
          state: "not_found",
          decision_operation_id: saved.request.decision_operation_id,
          branch_id: scope.branchId,
        },
      };
    });
    await expect(runPhysicalDisposition(scope, input, reviewed)).rejects.toThrow("изменилось");
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("serializes concurrent explicit decisions without issuing the second row", async () => {
    const other = { ...reviewed, line_id: uuid(90) };
    const results = await Promise.allSettled([
      runPhysicalDisposition(scope, input, reviewed),
      runPhysicalDisposition(scope, { ...input, target: physicalDispositionTarget(other) }, other),
    ]);
    expect(results.map((result) => result.status)).toEqual(["fulfilled", "rejected"]);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("acknowledgment needs fresh matching resolved GET; not_found and forgery never clear", async () => {
    const result = await runPhysicalDisposition(scope, input, reviewed);
    expect(await acknowledgePhysicalDisposition(scope, result)).toBe(false);
    mocks.get.mockResolvedValue({ data: result });
    expect(await acknowledgePhysicalDisposition(scope, { ...result, actor_id: uuid(90) })).toBe(
      false,
    );
    expect(pending()).toBeTruthy();
    expect(await acknowledgePhysicalDisposition(scope, result)).toBe(true);
    expect(loadPhysicalDisposition(scope)).toBeNull();
    expect(await acknowledgePhysicalDisposition(scope, result)).toBe(false);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("cannot clear after failed status or failed verified storage removal", async () => {
    const result = await runPhysicalDisposition(scope, input, reviewed);
    mocks.get.mockRejectedValueOnce(new Error("offline"));
    await expect(acknowledgePhysicalDisposition(scope, result)).rejects.toThrow("offline");
    mocks.get.mockResolvedValue({ data: result });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => undefined);
    await expect(acknowledgePhysicalDisposition(scope, result)).rejects.toThrow("удалить");
    expect(pending()).toBeTruthy();
  });
});
