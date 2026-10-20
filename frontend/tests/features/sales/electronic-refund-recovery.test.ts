import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ReconciledElectronicRefund } from "@/features/sales/electronicRefundConfirmations";
import {
  acknowledgeElectronicRefund,
  commandElectronicRefund,
  electronicRefundKey,
  electronicRefundReviewScope,
  inspectElectronicRefund,
  loadElectronicRefund,
  saveElectronicRefund,
  type ElectronicRefundRecovery,
  type PendingElectronicRefund,
} from "@/features/sales/electronicRefundRecovery";
import type {
  ElectronicRefundPreview,
  SavedElectronicRefundReview,
} from "@/features/sales/electronicRefundReview";

const mocks = vi.hoisted(() => ({
  generation: 0,
  refund: true,
  external: true,
  readonly: false,
  uuid: vi.fn(),
  shift: vi.fn(),
  save: vi.fn(),
  review: vi.fn(),
  bind: vi.fn(),
  binding: vi.fn(),
  cancel: vi.fn(),
  activate: vi.fn(),
  activation: vi.fn(),
  confirm: vi.fn(),
  confirmations: vi.fn(),
  post: vi.fn(),
  posting: vi.fn(),
}));
vi.mock("@/features/auth/permissions", () => ({
  hasPermissionForBranch: (_user: unknown, permission: string) =>
    permission === "pos.refund" ? mocks.refund : mocks.external,
}));
vi.mock("@/stores/auth", () => ({
  useAuthStore: {
    getState: () => ({ user: { support_access: { is_read_only: mocks.readonly } } }),
  },
}));
vi.mock("@/features/pos/api", () => ({ getCurrentShift: mocks.shift }));
vi.mock("@/features/pos/operationId", () => ({ generateUuidV4: mocks.uuid }));
vi.mock("@/features/pos/posStorageScope", () => ({
  capturePosSessionGuard: () => {
    const generation = mocks.generation;
    return () => generation === mocks.generation;
  },
}));
vi.mock("@/features/sales/electronicRefundReview", async (original) => ({
  ...(await original<typeof import("@/features/sales/electronicRefundReview")>()),
  saveElectronicRefundReview: mocks.save,
  readElectronicRefundReview: mocks.review,
}));
vi.mock("@/features/sales/electronicRefundBinding", () => ({
  bindElectronicRefundReview: mocks.bind,
  readElectronicRefundBinding: mocks.binding,
  cancelElectronicRefundBinding: mocks.cancel,
}));
vi.mock("@/features/sales/electronicRefundActivation", () => ({
  activateElectronicRefundReview: mocks.activate,
  readElectronicRefundActivation: mocks.activation,
}));
vi.mock("@/features/sales/electronicRefundConfirmations", () => ({
  readElectronicRefundConfirmations: mocks.confirmations,
  recordElectronicRefundConfirmation: mocks.confirm,
}));
vi.mock("@/features/sales/electronicRefundPosting", () => ({
  postElectronicRefund: mocks.post,
  readElectronicRefundPosting: mocks.posting,
}));
const id = (n: number): string => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const scope = {
  tenantId: id(1),
  userId: id(2),
  parentSaleId: id(3),
  branchId: id(4),
  registerId: id(5),
};
const narrowScope = {
  tenantId: scope.tenantId,
  userId: scope.userId,
  parentSaleId: scope.parentSaleId,
  branchId: scope.branchId,
};
const alive = () => true;
const preview: ElectronicRefundPreview = {
  state: "preview",
  parent_sale_id: scope.parentSaleId,
  branch_id: scope.branchId,
  original_receipt_number: "S-1",
  revision_hash: "a".repeat(64),
  total_amount: "3.99",
  currency: "TJS",
  lines: [
    {
      line_id: id(7),
      sale_format: "unit",
      quantity: 2,
      base_units: 2,
      offset: 0,
      gross_amount: "4.00",
      discount_amount: "0.01",
      net_amount: "3.99",
    },
  ],
  payments: [{ original_payment_id: id(8), method: "card", amount: "3.99", currency: "TJS" }],
};
const request = {
  operation_id: id(6),
  request: {
    parent_sale_id: scope.parentSaleId,
    selection: { items: [{ line_id: id(7), sale_format: "unit" as const, quantity: 2 }] },
    expected_total_amount: "3.99",
    expected_revision_hash: "a".repeat(64),
  },
};
const review: SavedElectronicRefundReview = {
  state: "saved_review",
  operation_id: id(6),
  saved_at: "2026-10-20T06:00:00Z",
  request: request.request,
  preview,
};
const binding = {
  state: "bound" as const,
  operation_id: id(6),
  parent_sale_id: scope.parentSaleId,
  review_revision_hash: preview.revision_hash,
  bound_at: "2026-10-20T06:00:01Z",
  cancelled_at: null,
};
const activation = {
  state: "activation_saved" as const,
  operation_id: id(6),
  parent_sale_id: scope.parentSaleId,
  branch_id: scope.branchId,
  actor_id: scope.userId,
  register_id: scope.registerId,
  shift_id: id(9),
  activated_at: "2026-10-20T06:00:02Z",
  review,
};
const evidence: ReconciledElectronicRefund = {
  state: "requires_reconciliation",
  activation,
  confirmations: [],
  missing_payment_ids: [id(8)],
};
const confirmation = {
  original_payment_id: id(8),
  terminal_id: "SYNTHETIC-TERMINAL",
  document_number: "SYNTHETIC-DOCUMENT",
};
const completeEvidence: ReconciledElectronicRefund = {
  state: "confirmed_evidence_only",
  activation,
  missing_payment_ids: [],
  confirmations: [
    {
      ...confirmation,
      method: "card",
      amount: "3.99",
      currency: "TJS",
      recorded_at: "2026-10-20T06:00:03Z",
    },
  ],
};
const details = { shift_id: id(9), reason_code: "other" as const, comment: "Operator comment" };
// Financial payload validation belongs to the separately tested strict posting client.
const posted = { state: "posted_refund", sale_id: id(10) };
const writes = () => [
  mocks.save,
  mocks.bind,
  mocks.cancel,
  mocks.activate,
  mocks.confirm,
  mocks.post,
];
const reads = () => [
  mocks.review,
  mocks.binding,
  mocks.activation,
  mocks.confirmations,
  mocks.posting,
];
function stored(phase: PendingElectronicRefund["attempted"] = "save"): PendingElectronicRefund {
  const pending: PendingElectronicRefund = { version: 1, scope, request, attempted: phase };
  localStorage.setItem(electronicRefundKey(scope), JSON.stringify(pending));
  return pending;
}
function current(phase: PendingElectronicRefund["attempted"]): ElectronicRefundRecovery {
  return { pending: stored(phase), review, binding, activation, evidence };
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
function expectNoCommands(): void {
  for (const command of writes()) expect(command).not.toHaveBeenCalled();
}
describe("durable electronic refund recovery", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    localStorage.clear();
    mocks.generation = 0;
    mocks.refund = true;
    mocks.external = true;
    mocks.readonly = false;
    serialLocks();
    mocks.shift.mockResolvedValue({
      id: id(9),
      status: "open",
      opened_by_user_id: scope.userId,
      branch_id: scope.branchId,
      register_id: scope.registerId,
      currency: "TJS",
    });
    mocks.uuid.mockReturnValue(id(6));
    mocks.save.mockResolvedValue(review);
    mocks.review.mockResolvedValue(review);
    mocks.bind.mockResolvedValue(binding);
    mocks.binding.mockResolvedValue(binding);
    mocks.cancel.mockResolvedValue({
      ...binding,
      state: "cancelled",
      cancelled_at: "2026-10-20T06:00:02Z",
    });
    mocks.activate.mockResolvedValue(activation);
    mocks.activation.mockResolvedValue(activation);
    mocks.confirm.mockResolvedValue(completeEvidence);
    mocks.confirmations.mockResolvedValue(evidence);
    mocks.post.mockResolvedValue(posted);
    mocks.posting.mockResolvedValue(posted);
  });
  it("persists one exact canonical request before the first command without receipt or evidence", async () => {
    mocks.save.mockImplementationOnce(() => {
      expect(loadElectronicRefund(scope)).toEqual({
        version: 1,
        scope,
        request,
        attempted: "save",
      });
      return Promise.resolve(review);
    });
    const result = await saveElectronicRefund(scope, preview, alive);
    expect(result.pending.request).toEqual(request);
    expect(mocks.save).toHaveBeenCalledWith(narrowScope, request);
    expect(localStorage.getItem(electronicRefundKey(scope))).not.toMatch(
      /terminal|document_number|receipt|cost|comment/,
    );
    expect(loadElectronicRefund(scope)).not.toBeNull();
  });
  it("uses four-field scopes for review/binding and five-field scopes for later clients", async () => {
    expect(electronicRefundReviewScope(scope)).toEqual(narrowScope);
    let result = await saveElectronicRefund(scope, preview, alive);
    result = await commandElectronicRefund(scope, result, { kind: "bind" }, alive);
    result = await commandElectronicRefund(
      scope,
      result,
      { kind: "activate", shiftId: id(9) },
      alive,
    );
    result = await commandElectronicRefund(scope, result, { kind: "confirm", confirmation }, alive);
    mocks.confirmations.mockResolvedValue(completeEvidence);
    result = await commandElectronicRefund(scope, result, { kind: "post", details }, alive);
    expect(result.posted).toEqual(posted);
    for (const client of [mocks.save, mocks.review, mocks.bind, mocks.binding]) {
      expect(client).toHaveBeenCalled();
      for (const call of client.mock.calls)
        expect(Object.keys(call[0] as object).sort()).toEqual(Object.keys(narrowScope).sort());
    }
    for (const client of [
      mocks.activate,
      mocks.activation,
      mocks.confirm,
      mocks.confirmations,
      mocks.post,
    ]) {
      expect(client).toHaveBeenCalled();
      for (const call of client.mock.calls) expect(call[0]).toEqual(scope);
    }
  });
  it.each(["save", "bind", "activate", "confirm", "post", "cancel"] as const)(
    "reload after lost %s response only reads the same operation",
    async (phase) => {
      const before =
        phase === "save"
          ? undefined
          : current(
              phase === "bind" || phase === "cancel"
                ? "save"
                : phase === "activate"
                  ? "bind"
                  : "activate",
            );
      if (phase === "post") mocks.confirmations.mockResolvedValue(completeEvidence);
      const client = {
        save: mocks.save,
        bind: mocks.bind,
        activate: mocks.activate,
        confirm: mocks.confirm,
        post: mocks.post,
        cancel: mocks.cancel,
      }[phase];
      client.mockRejectedValueOnce(new Error("Response lost"));
      const operation =
        phase === "save"
          ? saveElectronicRefund(scope, preview, alive)
          : commandElectronicRefund(
              scope,
              before!,
              phase === "activate"
                ? { kind: phase, shiftId: id(9) }
                : phase === "confirm"
                  ? { kind: phase, confirmation }
                  : phase === "post"
                    ? { kind: phase, details }
                    : { kind: phase },
              alive,
            );
      await expect(operation).rejects.toThrow("Response lost");
      const pending = loadElectronicRefund(scope);
      expect(pending?.request).toEqual(request);
      expect(pending?.attempted).toBe(phase);
      const counts = writes().map((write) => write.mock.calls.length);
      expect((await inspectElectronicRefund(scope))?.pending).toEqual(pending);
      expect(writes().map((write) => write.mock.calls.length)).toEqual(counts);
      expect(mocks.review).toHaveBeenLastCalledWith(narrowScope, request);
      await expect(saveElectronicRefund(scope, preview, alive)).rejects.toThrow("Журнал изменился");
      expect(mocks.uuid).toHaveBeenCalledTimes(phase === "save" ? 1 : 0);
    },
  );
  it.each([403, 404, 409, 500])(
    "retains pending on status %s without a new UUID or command",
    async (status) => {
      const pending = stored("post");
      mocks.posting.mockRejectedValueOnce(
        Object.assign(new Error("Unavailable"), { response: { status } }),
      );
      await expect(inspectElectronicRefund(scope)).rejects.toThrow("Unavailable");
      expect(loadElectronicRefund(scope)).toEqual(pending);
      await expect(saveElectronicRefund(scope, preview, alive)).rejects.toThrow();
      expect(mocks.uuid).not.toHaveBeenCalled();
      expectNoCommands();
    },
  );
  it("does not inspect or submit a damaged journal and never deletes it", async () => {
    localStorage.setItem(electronicRefundKey(scope), "{broken");
    await expect(inspectElectronicRefund(scope)).rejects.toThrow("повреждён");
    await expect(saveElectronicRefund(scope, preview, alive)).rejects.toThrow("повреждён");
    expect(localStorage.getItem(electronicRefundKey(scope))).toBe("{broken");
    for (const read of reads()) expect(read).not.toHaveBeenCalled();
    expectNoCommands();
  });
  it.each(["version", "scope", "request"])(
    "retains unsupported or corrupted %s metadata",
    async (field) => {
      const pending = stored();
      const bad =
        field === "version"
          ? { ...pending, version: 999 }
          : field === "scope"
            ? { ...pending, scope: { ...scope, userId: id(99) } }
            : {
                ...pending,
                request: { ...request, request: { ...request.request, parent_sale_id: id(99) } },
              };
      const raw = JSON.stringify(bad);
      localStorage.setItem(electronicRefundKey(scope), raw);
      await expect(inspectElectronicRefund(scope)).rejects.toThrow("повреждён");
      expect(localStorage.getItem(electronicRefundKey(scope))).toBe(raw);
      expectNoCommands();
    },
  );
  it("blocks commands when storage cannot persist the initial journal", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Quota");
    });
    await expect(saveElectronicRefund(scope, preview, alive)).rejects.toThrow("не отправлена");
    expectNoCommands();
  });
  it("blocks when a storage write cannot be read back", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
    await expect(saveElectronicRefund(scope, preview, alive)).rejects.toThrow("не отправлена");
    expectNoCommands();
  });
  it("blocks inspection and commands when storage reads are denied", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("Denied");
    });
    await expect(inspectElectronicRefund(scope)).rejects.toThrow("недоступно");
    await expect(saveElectronicRefund(scope, preview, alive)).rejects.toThrow("недоступно");
    expectNoCommands();
  });
  it.each([
    null,
    { status: "closed" },
    { opened_by_user_id: id(99) },
    { branch_id: id(99) },
    { register_id: id(99) },
    { currency: "USD" },
  ])(
    "blocks commands for an absent, closed or mismatched shift while allowing GET recovery",
    async (changed) => {
      current("post");
      const normal = await mocks.shift();
      mocks.shift.mockResolvedValue(changed === null ? null : { ...normal, ...changed });
      expect((await inspectElectronicRefund(scope))?.posted).toEqual(posted);
      await expect(
        commandElectronicRefund(scope, current("save"), { kind: "bind" }, alive),
      ).rejects.toThrow("открытая смена");
      expectNoCommands();
    },
  );
  it("stops a command after a session change during its shift lookup", async () => {
    const result = current("save");
    mocks.shift.mockImplementationOnce(() => {
      mocks.generation += 1;
      return Promise.resolve(null);
    });
    await expect(commandElectronicRefund(scope, result, { kind: "bind" }, alive)).rejects.toThrow(
      "Контекст изменился",
    );
    expect(loadElectronicRefund(scope)?.attempted).toBe("save");
    expectNoCommands();
  });
  it("requires Web Locks before inspecting or issuing commands", async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await expect(inspectElectronicRefund(scope)).rejects.toThrow("Web Locks");
    await expect(saveElectronicRefund(scope, preview, alive)).rejects.toThrow("Web Locks");
    expectNoCommands();
  });
  it("serializes simultaneous tabs into one UUID and one save command", async () => {
    const results = await Promise.allSettled([
      saveElectronicRefund(scope, preview, alive),
      saveElectronicRefund(scope, preview, alive),
    ]);
    expect(results.filter((result) => result.status === "fulfilled")).toHaveLength(1);
    expect(mocks.uuid).toHaveBeenCalledTimes(1);
    expect(mocks.save).toHaveBeenCalledTimes(1);
  });
  it("isolates all five scope fields", () => {
    stored();
    for (const field of Object.keys(scope) as Array<keyof typeof scope>) {
      const changed = { ...scope, [field]: id(99) };
      expect(electronicRefundKey(changed)).not.toBe(electronicRefundKey(scope));
      expect(loadElectronicRefund(changed)).toBeNull();
    }
  });
  it("rejects a late response after session generation changes and keeps the operation", async () => {
    stored("post");
    mocks.review.mockImplementationOnce(() => {
      mocks.generation += 1;
      return Promise.resolve(review);
    });
    await expect(inspectElectronicRefund(scope)).rejects.toThrow("Контекст изменился");
    expect(mocks.binding).not.toHaveBeenCalled();
    expect(loadElectronicRefund(scope)).not.toBeNull();
    expectNoCommands();
  });
  it("does not use a result after the modal unmounts", async () => {
    stored();
    let mounted = true;
    mocks.review.mockImplementationOnce(() => {
      mounted = false;
      return Promise.resolve(review);
    });
    await expect(inspectElectronicRefund(scope, () => mounted)).rejects.toThrow(
      "Контекст изменился",
    );
    expect(loadElectronicRefund(scope)).not.toBeNull();
    expectNoCommands();
  });
  it("rejects storage changes made while a GET is in flight", async () => {
    stored("bind");
    mocks.review.mockImplementationOnce(() => {
      stored("cancel");
      return Promise.resolve(review);
    });
    await expect(inspectElectronicRefund(scope)).rejects.toThrow("Журнал изменился");
    expect(mocks.binding).not.toHaveBeenCalled();
    expect(loadElectronicRefund(scope)?.attempted).toBe("cancel");
    expectNoCommands();
  });
  it("rechecks permission after awaited reads before a command", async () => {
    const result = current("save");
    mocks.review.mockImplementationOnce(() => {
      mocks.refund = false;
      return Promise.resolve(review);
    });
    await expect(commandElectronicRefund(scope, result, { kind: "bind" }, alive)).rejects.toThrow(
      "Доступ",
    );
    expectNoCommands();
  });
  it("permits readonly GET recovery while blocking new commands", async () => {
    stored("post");
    mocks.confirmations.mockResolvedValue(completeEvidence);
    mocks.readonly = true;
    expect((await inspectElectronicRefund(scope))?.posted).toEqual(posted);
    await expect(
      commandElectronicRefund(scope, current("save"), { kind: "bind" }, alive),
    ).rejects.toThrow("только для чтения");
    expectNoCommands();
  });
  it("requires external confirmation permission independently", async () => {
    const result = current("activate");
    mocks.external = false;
    await expect(
      commandElectronicRefund(scope, result, { kind: "confirm", confirmation }, alive),
    ).rejects.toThrow("Недостаточно прав");
    expect((await inspectElectronicRefund(scope))?.evidence).toEqual(evidence);
    expectNoCommands();
  });
  it("blocks post while confirmations are incomplete", async () => {
    await expect(
      commandElectronicRefund(scope, current("activate"), { kind: "post", details }, alive),
    ).rejects.toThrow("предыдущий этап");
    expectNoCommands();
  });
  it("does not reconfirm an already confirmed payment", async () => {
    mocks.confirmations.mockResolvedValue(completeEvidence);
    await expect(
      commandElectronicRefund(scope, current("confirm"), { kind: "confirm", confirmation }, alive),
    ).rejects.toThrow("уже подтверждена");
    expectNoCommands();
  });
  it("cannot cancel after activation", async () => {
    await expect(
      commandElectronicRefund(scope, current("activate"), { kind: "cancel" }, alive),
    ).rejects.toThrow("предыдущий этап");
    expectNoCommands();
  });
  it("does not send a command if persisting its phase fails", async () => {
    const result = current("save");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Quota");
    });
    await expect(commandElectronicRefund(scope, result, { kind: "bind" }, alive)).rejects.toThrow(
      "не отправлена",
    );
    expect(loadElectronicRefund(scope)?.attempted).toBe("save");
    expectNoCommands();
  });
  it("acknowledges posted only after fresh GETs and removes only its journal", async () => {
    stored("post");
    mocks.confirmations.mockResolvedValue(completeEvidence);
    expect(await acknowledgeElectronicRefund(scope, id(6), alive)).toBe(posted.sale_id);
    for (const read of reads()) expect(read).toHaveBeenCalledTimes(1);
    expect(loadElectronicRefund(scope)).toBeNull();
    expectNoCommands();
  });
  it("cannot acknowledge an unknown or mismatched operation", async () => {
    stored("post");
    mocks.posting.mockRejectedValueOnce(new Error("Response lost"));
    await expect(acknowledgeElectronicRefund(scope, id(99), alive)).rejects.toThrow("изменилась");
    await expect(acknowledgeElectronicRefund(scope, id(6), alive)).rejects.toThrow("Response lost");
    expect(loadElectronicRefund(scope)).not.toBeNull();
    expectNoCommands();
  });
  it("retains pending when acknowledgment removal is denied", async () => {
    stored("post");
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("Denied");
    });
    await expect(acknowledgeElectronicRefund(scope, id(6), alive)).rejects.toThrow("оставлен");
    expect(loadElectronicRefund(scope)).not.toBeNull();
    expectNoCommands();
  });
  it("allows acknowledging verified cancellation but never an unposted review", async () => {
    stored("save");
    await expect(acknowledgeElectronicRefund(scope, id(6), alive)).rejects.toThrow(
      "не подтверждено",
    );
    stored("cancel");
    mocks.binding.mockResolvedValue({
      ...binding,
      state: "cancelled",
      cancelled_at: "2026-10-20T06:00:04Z",
    });
    expect(await acknowledgeElectronicRefund(scope, id(6), alive)).toBe(scope.parentSaleId);
    expect(loadElectronicRefund(scope)).toBeNull();
    expectNoCommands();
  });
});
