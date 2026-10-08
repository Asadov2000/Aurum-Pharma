import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  acknowledgePhysicalCash,
  loadPendingPhysicalCash,
  physicalCashRecoveryKey,
  runPhysicalCash,
} from "@/features/pos/physicalCashRecovery";

const mocks = vi.hoisted(() => ({
  generation: 0,
  current: true,
  status: vi.fn(),
  save: vi.fn(),
  complete: vi.fn(),
}));
vi.mock("@/features/pos/posStorageScope", async (original) => ({
  ...(await original<typeof import("@/features/pos/posStorageScope")>()),
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => mocks.current && generation === mocks.generation;
  },
}));
vi.mock("@/features/pos/physicalCashCheckout", async (original) => ({
  ...(await original<typeof import("@/features/pos/physicalCashCheckout")>()),
  getPhysicalCashOperation: mocks.status,
  savePhysicalCashIntent: mocks.save,
  completePhysicalCash: mocks.complete,
}));

const scope = {
  tenantId: "10000000-0000-4000-8000-000000000001",
  userId: "10000000-0000-4000-8000-000000000002",
};
const saleId = "10000000-0000-4000-8000-000000000003";
const input = {
  schema_version: 2 as const,
  sale_id: saleId,
  expected_revision_hash: "a".repeat(64),
  expected_total_amount: "12.50",
  cash_tendered: "20.00",
  currency: "TJS" as const,
};
const result = { completed: true };
const key = physicalCashRecoveryKey(saleId, scope);

function pending() {
  const value = loadPendingPhysicalCash(saleId, scope);
  if (!value) throw new Error("Missing test payment");
  return value;
}

function serialLocks(): void {
  let tail: Promise<unknown> = Promise.resolve();
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: (_name: string, execute: () => Promise<unknown>) => {
        const operation = tail.then(execute);
        tail = operation.catch(() => undefined);
        return operation;
      },
    },
  });
}

describe("physical cash durable recovery", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    localStorage.clear();
    mocks.generation = 0;
    mocks.current = true;
    mocks.status.mockResolvedValue({ state: "not_found" });
    mocks.save.mockResolvedValue({});
    mocks.complete.mockImplementation(() => {
      mocks.status.mockResolvedValue({ state: "completed", result });
      return Promise.resolve(result);
    });
    serialLocks();
  });

  it("persists the fixed full request before even the first GET and retains it after completion", async () => {
    mocks.status.mockImplementation(() => {
      expect(pending().request.cash_tendered).toBe("20.00");
      return Promise.resolve(
        mocks.complete.mock.calls.length ? { state: "completed", result } : { state: "not_found" },
      );
    });
    expect(await runPhysicalCash(saleId, scope, input)).toEqual(result);
    expect(mocks.save).toHaveBeenCalledWith(pending().request, scope);
    expect(mocks.complete).toHaveBeenCalledWith(pending().request, scope);
    expect(Object.keys(JSON.parse(localStorage.getItem(key)!))).toEqual([
      "version",
      "tenantId",
      "userId",
      "createdAt",
      "request",
    ]);
  });

  it("recovers a lost completion response with GET only, using the original UUID", async () => {
    mocks.complete.mockRejectedValueOnce(new Error("lost response"));
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("lost response");
    const request = pending().request;
    mocks.status.mockResolvedValue({ state: "completed", result });
    expect(await runPhysicalCash(saleId, scope)).toEqual(result);
    expect(mocks.status).toHaveBeenLastCalledWith(request, scope);
    expect(mocks.save).toHaveBeenCalledTimes(1);
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });

  it("continues saved intent without resaving it or reading a live draft/shift", async () => {
    mocks.status.mockResolvedValue({ state: "saved_draft" });
    await runPhysicalCash(saleId, scope, input);
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.complete).toHaveBeenCalledWith(pending().request, scope);
  });

  it("retries an unreceived intent with exactly the same UUID and amounts", async () => {
    mocks.save.mockRejectedValueOnce(new Error("offline"));
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("offline");
    const request = pending().request;
    await runPhysicalCash(saleId, scope);
    expect(mocks.save.mock.calls.map(([payload]) => payload)).toEqual([request, request]);
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });

  it("serializes double clicks into one UUID; a completed recovery never POSTs again", async () => {
    mocks.complete.mockImplementation(() => {
      mocks.status.mockResolvedValue({ state: "completed", result });
      return Promise.resolve(result);
    });
    expect(
      await Promise.all([
        runPhysicalCash(saleId, scope, input),
        runPhysicalCash(saleId, scope, input),
      ]),
    ).toEqual([result, result]);
    expect(mocks.save).toHaveBeenCalledTimes(1);
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });

  it("rejects changed tender/total without replacing unresolved intent", async () => {
    await runPhysicalCash(saleId, scope, input);
    const raw = localStorage.getItem(key);
    await expect(
      runPhysicalCash(saleId, scope, { ...input, cash_tendered: "30.00" }),
    ).rejects.toThrow("незавершённая");
    expect(localStorage.getItem(key)).toBe(raw);
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });

  it.each(["{", "{}", JSON.stringify({ version: 1, request: input })])(
    "keeps corrupt financial data and blocks requests: %s",
    async (raw) => {
      localStorage.setItem(key, raw);
      await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("повреждена");
      expect(localStorage.getItem(key)).toBe(raw);
      expect(mocks.status).not.toHaveBeenCalled();
    },
  );

  it("rejects owner/sale mismatch even when stored under the current key", async () => {
    await runPhysicalCash(saleId, scope, input);
    localStorage.setItem(key, JSON.stringify({ ...pending(), userId: saleId }));
    await expect(runPhysicalCash(saleId, scope)).rejects.toThrow("повреждена");
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });

  it("does not expire an intent after a month", async () => {
    await runPhysicalCash(saleId, scope, input);
    const value = pending();
    localStorage.setItem(key, JSON.stringify({ ...value, createdAt: 1 }));
    expect(pending().request).toEqual(value.request);
  });

  it("isolates another account and pharmacy", async () => {
    await runPhysicalCash(saleId, scope, input);
    expect(loadPendingPhysicalCash(saleId, { ...scope, userId: saleId })).toBeNull();
    expect(loadPendingPhysicalCash(saleId, { ...scope, tenantId: saleId })).toBeNull();
  });

  it("fails closed on read failure", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("прочитать");
    expect(mocks.status).not.toHaveBeenCalled();
  });

  it.each(["throw", "ignored"])("does not send payment if durable write is %s", async (mode) => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      if (mode === "throw") throw new Error("quota");
    });
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("сохранить");
    expect(mocks.status).not.toHaveBeenCalled();
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("keeps the intent if a status query fails", async () => {
    mocks.status.mockRejectedValue(new Error("offline"));
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("offline");
    expect(pending().request.cash_tendered).toBe("20.00");
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it.each(["status", "save", "complete"])(
    "rejects a previous session after %s await",
    async (phase) => {
      const target =
        phase === "status" ? mocks.status : phase === "save" ? mocks.save : mocks.complete;
      target.mockImplementation(() => {
        mocks.generation += 1;
        return Promise.resolve(phase === "status" ? { state: "not_found" } : result);
      });
      await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("Аккаунт изменился");
      expect(pending()).toBeDefined();
      if (phase !== "complete") expect(mocks.complete).not.toHaveBeenCalled();
    },
  );

  it("blocks a queued operation after account changes while waiting for the lock", async () => {
    mocks.status.mockImplementationOnce(() => {
      mocks.generation += 1;
      return Promise.resolve({ state: "not_found" });
    });
    const outcomes = await Promise.allSettled([
      runPhysicalCash(saleId, scope, input),
      runPhysicalCash(saleId, scope, input),
    ]);
    expect(outcomes.every((outcome) => outcome.status === "rejected")).toBe(true);
    expect(mocks.status).toHaveBeenCalledTimes(1);
  });

  it("detects queue replacement after a server await", async () => {
    mocks.status.mockImplementation(() => {
      localStorage.setItem(key, JSON.stringify({ ...pending(), createdAt: 1 }));
      return Promise.resolve({ state: "not_found" });
    });
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("изменилась");
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("fails closed without cross-tab atomic locks", async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("безопасной оплаты");
    expect(localStorage.getItem(key)).toBeNull();
    expect(mocks.status).not.toHaveBeenCalled();
  });

  it("clears only explicitly acknowledged and server-confirmed completed payment", async () => {
    await runPhysicalCash(saleId, scope, input);
    const id = pending().request.operation_id;
    mocks.status.mockResolvedValue({ state: "not_found" });
    expect(await acknowledgePhysicalCash(saleId, id, scope)).toBe(false);
    mocks.status.mockResolvedValue({ state: "saved_draft" });
    expect(await acknowledgePhysicalCash(saleId, id, scope)).toBe(false);
    expect(pending()).toBeDefined();
    mocks.status.mockResolvedValue({ state: "completed", result });
    expect(await acknowledgePhysicalCash(saleId, id, scope)).toBe(true);
    expect(loadPendingPhysicalCash(saleId, scope)).toBeNull();
  });

  it("never clears another UUID or a payment after a session change", async () => {
    await runPhysicalCash(saleId, scope, input);
    expect(await acknowledgePhysicalCash(saleId, saleId, scope)).toBe(false);
    mocks.status.mockImplementation(() => {
      mocks.generation += 1;
      return Promise.resolve({ state: "completed", result });
    });
    await expect(
      acknowledgePhysicalCash(saleId, pending().request.operation_id, scope),
    ).rejects.toThrow("Аккаунт");
    expect(pending()).toBeDefined();
  });

  it("reports failed removal without forgetting the completed operation", async () => {
    await runPhysicalCash(saleId, scope, input);
    mocks.status.mockResolvedValue({ state: "completed", result });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(await acknowledgePhysicalCash(saleId, pending().request.operation_id, scope)).toBe(
      false,
    );
    expect(pending()).toBeDefined();
  });

  it.each(["error", "saved_draft"])("retains intent if final verification is %s", async (state) => {
    mocks.complete.mockImplementation(() => {
      if (state === "error") mocks.status.mockRejectedValue(new Error("verification offline"));
      else mocks.status.mockResolvedValue({ state });
      return Promise.resolve(result);
    });
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow();
    expect(pending().request.cash_tendered).toBe("20.00");
    expect(mocks.complete).toHaveBeenCalledTimes(1);
  });

  it("does not send HTTP when read-back fails after a successful write", async () => {
    const read = Storage.prototype.getItem;
    let reads = 0;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (
      this: Storage,
      storageKey: string,
    ) {
      reads += 1;
      if (reads === 2) throw new Error("read-back blocked");
      return read.call(this, storageKey);
    });
    await expect(runPhysicalCash(saleId, scope, input)).rejects.toThrow("сохранить");
    expect(pending()).toBeDefined();
    expect(mocks.status).not.toHaveBeenCalled();
  });

  it("detects silently ignored removal", async () => {
    await runPhysicalCash(saleId, scope, input);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => undefined);
    expect(await acknowledgePhysicalCash(saleId, pending().request.operation_id, scope)).toBe(
      false,
    );
    expect(pending()).toBeDefined();
  });
});
