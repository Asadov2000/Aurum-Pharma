import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  completePhysicalWriteoff,
  getPhysicalWriteoffStatus,
  getPhysicalWriteoffStock,
  physicalWriteoffInputSchema,
  physicalWriteoffStockSchema,
  type PhysicalWriteoffRequest,
} from "@/features/inventory/physicalWriteoff";
import {
  acknowledgePhysicalWriteoff,
  inspectPhysicalWriteoff,
  loadPhysicalWriteoff,
  physicalWriteoffKey,
  runPhysicalWriteoff,
} from "@/features/inventory/physicalWriteoffRecovery";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  generation: 0,
  config: vi.fn((value: unknown) => value),
}));
vi.mock("@/lib/api", () => ({
  api: { get: mocks.get, post: mocks.post },
  withAuthSession: mocks.config,
}));
vi.mock("@/features/pos/posStorageScope", () => ({
  currentPosStorageScope: () => ({
    tenantId: "00000000-0000-4000-8000-000000000001",
    userId: "00000000-0000-4000-8000-000000000002",
  }),
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
}));
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const scope = { tenantId: id(1), userId: id(2), branchId: id(3) };
const input = {
  branch_id: id(3),
  lot_id: id(4),
  reason: "damaged" as const,
  comment: "  Broken carton  ",
  selection: { closed_packs: 1, opened_packs: [] },
};
const request: PhysicalWriteoffRequest = { ...input, operation_id: id(5) };
const result = (command: PhysicalWriteoffRequest = request) => ({
  operation_id: command.operation_id,
  branch_id: command.branch_id,
  lot_id: command.lot_id,
  reason: command.reason,
  comment: command.comment,
  base_units: "30",
  closed_packs: "1",
  created_at: "2026-10-16T10:00:00Z",
});
const absent = () => Promise.reject({ isAxiosError: true, response: { status: 404 } });
function stock() {
  return {
    lot: {
      lot_id: id(4),
      branch_id: id(3),
      catalog_id: id(6),
      catalog_name: "Medicine",
      expires_at: "2026-01-01",
      packaging: {
        version: 2,
        unit: "tablet",
        units_per_pack: 30,
        units_per_blister: 10,
        dispense_step: 1,
      },
      closed_packs: "1",
      opened_units: "29",
      base_units: "59",
      is_blocked: false,
      blocking_revision: "0",
    },
    opened_packs: [
      {
        pack_id: id(7),
        remaining_units: "29",
        state: {
          intact_blisters: [2, 3],
          opened_blisters: [{ number: 1, remaining_units: 9 }],
          loose_units: 0,
        },
      },
    ],
    total: 1,
    page: 1,
    page_size: 50,
  };
}
beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.config.mockClear();
  mocks.generation = 0;
  let tail: Promise<unknown> = Promise.resolve();
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: (_key: string, callback: () => Promise<unknown>) => {
        const next = tail.then(callback);
        tail = next.catch(() => undefined);
        return next;
      },
    },
  });
  mocks.get.mockImplementation(absent);
  mocks.post.mockImplementation((_url: string, command: PhysicalWriteoffRequest) =>
    Promise.resolve({ data: result(command) }),
  );
});

describe("physical write-off contracts", () => {
  it("keeps exact comments and rejects caller authority and monetary fields", () => {
    expect(physicalWriteoffInputSchema.parse(input).comment).toBe(input.comment);
    for (const extra of [{ actor_id: id(1) }, { cost_amount: "1.00" }, { tenant_id: id(1) }])
      expect(() => physicalWriteoffInputSchema.parse({ ...input, ...extra })).toThrow();
  });
  it.each(["bad\0", "bad\ud800", "x".repeat(2001)])("rejects unsafe comment %s", (comment) => {
    expect(() => physicalWriteoffInputSchema.parse({ ...input, comment })).toThrow();
  });
  it.each([true, "1", -1, 1.5])("rejects noninteger count %s", (count) => {
    expect(() =>
      physicalWriteoffInputSchema.parse({
        ...input,
        selection: { closed_packs: count, opened_packs: [] },
      }),
    ).toThrow();
  });
  it("rejects duplicate packs and empty selections", () => {
    const pack = { pack_id: id(7), loose_units: 1, blisters: [] };
    for (const selection of [
      { closed_packs: 0, opened_packs: [] },
      { closed_packs: 0, opened_packs: [pack, pack] },
    ])
      expect(() => physicalWriteoffInputSchema.parse({ ...input, selection })).toThrow();
  });
  it("reads expired physical stock with authenticated authority", async () => {
    mocks.get.mockResolvedValue({ data: stock() });
    expect((await getPhysicalWriteoffStock(id(4), id(3))).opened_packs).toHaveLength(1);
    expect(mocks.config).toHaveBeenCalledWith(
      expect.objectContaining({ params: { branch_id: id(3), page: 1, page_size: 50 } }),
      true,
    );
  });
  it("rejects another lot or page", async () => {
    mocks.get.mockResolvedValue({ data: { ...stock(), page: 2 } });
    await expect(getPhysicalWriteoffStock(id(4), id(3))).rejects.toThrow();
    const data = stock();
    data.lot.lot_id = id(9);
    mocks.get.mockResolvedValue({ data });
    await expect(getPhysicalWriteoffStock(id(4), id(3))).rejects.toThrow();
  });
  it("rejects invented pack totals, duplicate blister identities, and monetary fields", () => {
    const data = stock();
    data.opened_packs[0]!.remaining_units = "28";
    expect(() => physicalWriteoffStockSchema.parse(data)).toThrow();
    const duplicate = stock();
    duplicate.opened_packs[0]!.state.intact_blisters.push(1);
    expect(() => physicalWriteoffStockSchema.parse(duplicate)).toThrow();
    expect(() => physicalWriteoffStockSchema.parse({ ...stock(), cost: "private" })).toThrow();
  });
  it.each([
    "operation_id",
    "branch_id",
    "lot_id",
    "reason",
    "comment",
    "base_units",
    "closed_packs",
  ])("rejects mismatching result %s", async (field) => {
    mocks.post.mockResolvedValue({
      data: { ...result(), [field]: field.endsWith("id") ? id(9) : "other" },
    });
    await expect(completePhysicalWriteoff(scope, request, "30")).rejects.toThrow();
  });
  it("does not turn forbidden or malformed results into missing", async () => {
    mocks.get.mockRejectedValue({ isAxiosError: true, response: { status: 403 } });
    await expect(getPhysicalWriteoffStatus(scope, request, "30")).rejects.toBeDefined();
    mocks.get.mockResolvedValue({ data: { ...result(), cost_amount: "private" } });
    await expect(getPhysicalWriteoffStatus(scope, request, "30")).rejects.toThrow();
  });
  it("discards a response after logout even if account identities return", async () => {
    mocks.post.mockImplementation(() => {
      mocks.generation++;
      return Promise.resolve({ data: result() });
    });
    await expect(completePhysicalWriteoff(scope, request, "30")).rejects.toThrow(
      "Аккаунт изменился",
    );
  });
});

describe("durable write-off recovery", () => {
  it("releases a definitively rejected first submission so the stock choice can be corrected", async () => {
    mocks.post.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 422, data: { error: { code: "business_rule_violation" } } },
    });
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toBeDefined();
    expect(loadPhysicalWriteoff(scope)).toBeNull();
    await runPhysicalWriteoff(scope, input, "30");
    expect(mocks.post.mock.calls[0]?.[1].operation_id).not.toBe(
      mocks.post.mock.calls[1]?.[1].operation_id,
    );
  });
  it("never releases a retry rejection after an uncertain original submission", async () => {
    mocks.post.mockRejectedValueOnce(new Error("response lost"));
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toThrow();
    const pending = loadPhysicalWriteoff(scope);
    mocks.post.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 422, data: { error: { code: "business_rule_violation" } } },
    });
    await expect(runPhysicalWriteoff(scope)).rejects.toBeDefined();
    expect(loadPhysicalWriteoff(scope)).toEqual(pending);
  });
  it.each([403, 409, 500, 422])(
    "does not release an untyped or uncertain rejection %s",
    async (status) => {
      mocks.post.mockRejectedValueOnce({ isAxiosError: true, response: { status, data: {} } });
      await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toBeDefined();
      expect(loadPhysicalWriteoff(scope)).not.toBeNull();
    },
  );
  it("does not read another user's pending command", async () => {
    await runPhysicalWriteoff(scope, input, "30");
    expect(loadPhysicalWriteoff({ ...scope, userId: id(9) })).toBeNull();
    expect(loadPhysicalWriteoff(scope)).not.toBeNull();
  });
  it("fails closed when reading storage is denied", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toThrow("прочитать");
    expect(mocks.get).not.toHaveBeenCalled();
  });
  it("does not operate without cross-tab locks", async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toThrow("современный браузер");
    expect(mocks.get).not.toHaveBeenCalled();
    expect(loadPhysicalWriteoff(scope)).toBeNull();
  });
  it("persists exact request before network and keeps it until confirmed acknowledgement", async () => {
    mocks.get.mockImplementation(() => {
      expect(loadPhysicalWriteoff(scope)?.request.comment).toBe(input.comment);
      return absent();
    });
    const saved = await runPhysicalWriteoff(scope, input, "30");
    expect(loadPhysicalWriteoff(scope)?.request.operation_id).toBe(saved.operation_id);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    mocks.get.mockResolvedValue({ data: saved });
    await acknowledgePhysicalWriteoff(scope);
    expect(loadPhysicalWriteoff(scope)).toBeNull();
  });
  it("lost response reload recovers via GET and never repeats POST automatically", async () => {
    let committed: ReturnType<typeof result> | undefined;
    mocks.post.mockImplementation((_url: string, command: PhysicalWriteoffRequest) => {
      committed = result(command);
      return Promise.reject(new Error("network lost"));
    });
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toThrow("network lost");
    mocks.get.mockResolvedValue({ data: committed });
    expect(await inspectPhysicalWriteoff(scope)).toEqual(committed);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("404 preserves UUID and explicit retry uses the exact persisted command", async () => {
    mocks.post.mockRejectedValueOnce(new Error("network"));
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toThrow();
    const pending = loadPhysicalWriteoff(scope);
    expect(await inspectPhysicalWriteoff(scope)).toBeNull();
    await expect(acknowledgePhysicalWriteoff(scope)).rejects.toThrow("не подтверждён");
    expect(loadPhysicalWriteoff(scope)).toEqual(pending);
    await runPhysicalWriteoff(scope);
    expect(mocks.post.mock.calls[0]?.[1]).toEqual(mocks.post.mock.calls[1]?.[1]);
  });
  it("blocks altered requests, altered amounts, and another branch", async () => {
    await runPhysicalWriteoff(scope, input, "30");
    await expect(runPhysicalWriteoff(scope, { ...input, reason: "other" }, "30")).rejects.toThrow();
    await expect(runPhysicalWriteoff(scope, input, "31")).rejects.toThrow();
    await expect(runPhysicalWriteoff({ ...scope, branchId: id(9) })).rejects.toThrow(
      "другой точке",
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("rejects corrupt storage without erasing the recovery barrier", async () => {
    localStorage.setItem(physicalWriteoffKey(scope), "{bad");
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toThrow("повреждено");
    expect(localStorage.getItem(physicalWriteoffKey(scope))).toBe("{bad");
    expect(mocks.get).not.toHaveBeenCalled();
  });
  it("fails closed when storage cannot be persisted", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toThrow("Запрос не отправлен");
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it("does not send when logout happens during initial GET", async () => {
    mocks.get.mockImplementation(() => {
      mocks.generation++;
      return absent();
    });
    await expect(runPhysicalWriteoff(scope, input, "30")).rejects.toThrow("Аккаунт изменился");
    expect(mocks.post).not.toHaveBeenCalled();
    expect(loadPhysicalWriteoff(scope)).not.toBeNull();
  });
  it("serializes two confirmations and returns saved result without duplicate mutation", async () => {
    let committed: ReturnType<typeof result> | null = null;
    mocks.get.mockImplementation(() =>
      committed ? Promise.resolve({ data: committed }) : absent(),
    );
    mocks.post.mockImplementation((_url: string, command: PhysicalWriteoffRequest) => {
      committed = result(command);
      return Promise.resolve({ data: committed });
    });
    const results = await Promise.all([
      runPhysicalWriteoff(scope, input, "30"),
      runPhysicalWriteoff(scope, input, "30"),
    ]);
    expect(results[0]).toEqual(results[1]);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("storage replacement during a request cannot release another operation", async () => {
    const saved = await runPhysicalWriteoff(scope, input, "30");
    mocks.get.mockImplementation(() => {
      localStorage.removeItem(physicalWriteoffKey(scope));
      return Promise.resolve({ data: saved });
    });
    await expect(acknowledgePhysicalWriteoff(scope)).rejects.toThrow("изменилось");
  });
});
