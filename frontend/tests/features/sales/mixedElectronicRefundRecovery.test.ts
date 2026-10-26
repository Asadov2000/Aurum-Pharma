import { mixedElectronicRefundPreviewSchema } from "@/features/sales/mixedElectronicRefundReview";
import { createHash, webcrypto } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ReconciledMixedElectronicRefund } from "@/features/sales/mixedElectronicRefundConfirmations";
import {
  acknowledgeMixedElectronicRefund,
  commandMixedElectronicRefund,
  mixedElectronicRefundKey,
  mixedElectronicRefundReviewScope,
  inspectMixedElectronicRefund,
  loadMixedElectronicRefund,
  saveMixedElectronicRefund,
  type MixedElectronicRefundRecovery,
  type PendingMixedElectronicRefund,
} from "@/features/sales/mixedElectronicRefundRecovery";
import type {
  MixedElectronicRefundPreview,
  SavedMixedElectronicRefundReview,
} from "@/features/sales/mixedElectronicRefundReview";

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
    getState: () => ({
      accessToken: "synthetic-token",
      user: { support_access: { is_read_only: mocks.readonly } },
    }),
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
vi.mock("@/features/sales/mixedElectronicRefundReview", async (original) => ({
  ...(await original<typeof import("@/features/sales/mixedElectronicRefundReview")>()),
  saveMixedElectronicRefundReview: mocks.save,
  readMixedElectronicRefundReview: mocks.review,
}));
vi.mock("@/features/sales/mixedElectronicRefundBinding", () => ({
  bindMixedElectronicRefundReview: mocks.bind,
  readMixedElectronicRefundBinding: mocks.binding,
  cancelMixedElectronicRefundBinding: mocks.cancel,
}));
vi.mock("@/features/sales/mixedElectronicRefundActivation", () => ({
  activateMixedElectronicRefundReview: mocks.activate,
  readMixedElectronicRefundActivation: mocks.activation,
}));
vi.mock("@/features/sales/mixedElectronicRefundConfirmations", () => ({
  readMixedElectronicRefundConfirmations: mocks.confirmations,
  recordMixedElectronicRefundConfirmation: mocks.confirm,
}));
vi.mock("@/features/sales/mixedElectronicRefundPosting", () => ({
  postMixedElectronicRefund: mocks.post,
  readMixedElectronicRefundPosting: mocks.posting,
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
  registerId: scope.registerId,
};
const alive = () => true;
const preview: MixedElectronicRefundPreview = {
  state: "preview",
  parent_sale_id: scope.parentSaleId,
  branch_id: scope.branchId,
  original_receipt_number: "S-1",
  revision_hash: "a".repeat(64),
  total_amount: "3.99",
  currency: "TJS",
  lines: [
    {
      inventory_kind: "physical_unit" as const,
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
    selection: {
      items: [
        {
          inventory_kind: "physical_unit" as const,
          line_id: id(7),
          sale_format: "unit" as const,
          quantity: 2,
        },
      ],
    },
    expected_total_amount: "3.99",
    expected_revision_hash: "a".repeat(64),
  },
};
const review: SavedMixedElectronicRefundReview = {
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
const evidence: ReconciledMixedElectronicRefund = {
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
const completeEvidence: ReconciledMixedElectronicRefund = {
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
const posted = { state: "posted_refund", sale_id: id(10), shift_id: id(9) };
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
function stored(
  phase: PendingMixedElectronicRefund["attempted"] = "save",
): PendingMixedElectronicRefund {
  const pending: PendingMixedElectronicRefund = {
    version: 1,
    activationShiftId: ["activate", "confirm", "post"].includes(phase) ? id(9) : null,
    postingShiftId: phase === "post" ? id(9) : null,
    previewHash: createHash("sha256")
      .update(JSON.stringify(mixedElectronicRefundPreviewSchema.parse(preview)))
      .digest("hex"),
    scope,
    request,
    attempted: phase,
  };
  localStorage.setItem(mixedElectronicRefundKey(scope), JSON.stringify(pending));
  return pending;
}
function current(phase: PendingMixedElectronicRefund["attempted"]): MixedElectronicRefundRecovery {
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
describe("durable mixed electronic refund recovery", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(globalThis.crypto, "subtle", {
      configurable: true,
      value: webcrypto.subtle,
    });
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
      expect(loadMixedElectronicRefund(scope)).toEqual({
        version: 1,
        activationShiftId: null,
        postingShiftId: null,
        previewHash: expect.stringMatching(/^[0-9a-f]{64}$/),
        scope,
        request,
        attempted: "save",
      });
      return Promise.resolve(review);
    });
    const result = await saveMixedElectronicRefund(
      scope,
      request.request.selection,
      preview,
      alive,
    );
    expect(result.pending.request).toEqual(request);
    expect(mocks.save).toHaveBeenCalledWith(
      expect.objectContaining({ ...narrowScope, contextCheck: expect.any(Function) }),
      request,
    );
    expect(localStorage.getItem(mixedElectronicRefundKey(scope))).not.toMatch(
      /terminal|document_number|receipt|cost|comment/,
    );
    expect(loadMixedElectronicRefund(scope)).not.toBeNull();
  });
  it("preserves quoted selection order when the server orders preview lines differently", async () => {
    const selection = {
      items: [
        ...request.request.selection.items,
        { inventory_kind: "legacy_pack" as const, line_id: id(20), quantity: "1.000" },
      ],
    };
    const quotedPreview: MixedElectronicRefundPreview = {
      ...preview,
      revision_hash: "b".repeat(64),
      total_amount: "4.99",
      lines: [
        {
          inventory_kind: "legacy_pack",
          line_id: id(20),
          quantity: "1.000",
          offset: "0.000",
          gross_amount: "1.00",
          discount_amount: "0.00",
          net_amount: "1.00",
        },
        ...preview.lines,
      ],
      payments: [{ ...preview.payments[0]!, amount: "4.99" }],
    };
    const quotedRequest = {
      ...request,
      request: {
        ...request.request,
        selection: structuredClone(selection),
        expected_total_amount: quotedPreview.total_amount,
        expected_revision_hash: quotedPreview.revision_hash,
      },
    };
    const quotedReview = { ...review, request: quotedRequest.request, preview: quotedPreview };
    mocks.save.mockResolvedValueOnce(quotedReview);
    mocks.review.mockResolvedValueOnce(quotedReview);
    const saving = saveMixedElectronicRefund(scope, selection, quotedPreview, alive);
    selection.items.reverse();
    const saved = await saving;
    expect(saved.pending.request).toEqual(quotedRequest);
    expect(mocks.save).toHaveBeenCalledWith(
      { ...scope, contextCheck: expect.any(Function) },
      quotedRequest,
    );
    const recovered = await inspectMixedElectronicRefund(scope, alive);
    expect(recovered?.pending.request).toEqual(quotedRequest);
    expect(mocks.review).toHaveBeenCalledWith(scope, quotedRequest);
    expect(mocks.save).toHaveBeenCalledTimes(1);
    expect(loadMixedElectronicRefund(scope)?.request).toEqual(quotedRequest);
  });
  it.each([
    { inventory_kind: "physical_unit", line_id: id(7), sale_format: "unit", quantity: 1 },
    { inventory_kind: "physical_unit", line_id: id(7), sale_format: "pack", quantity: 2 },
    { inventory_kind: "legacy_pack", line_id: id(7), quantity: "2.000" },
    { inventory_kind: "physical_unit", line_id: id(21), sale_format: "unit", quantity: 2 },
    { inventory_kind: "physical_unit", line_id: "invalid", sale_format: "unit", quantity: 2 },
    { inventory_kind: "physical_unit", line_id: id(7), sale_format: "unit", quantity: "2" },
  ])("rejects a changed or malformed quoted selection before saving: %j", async (item) => {
    await expect(async () =>
      saveMixedElectronicRefund(
        scope,
        { items: [item] } as unknown as typeof request.request.selection,
        preview,
        alive,
      ),
    ).rejects.toThrow();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(localStorage.getItem(mixedElectronicRefundKey(scope))).toBeNull();
  });
  it("rejects duplicate quoted lines before saving", async () => {
    await expect(async () =>
      saveMixedElectronicRefund(
        scope,
        { items: [...request.request.selection.items, ...request.request.selection.items] },
        preview,
        alive,
      ),
    ).rejects.toThrow();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(localStorage.getItem(mixedElectronicRefundKey(scope))).toBeNull();
  });
  it("passes the exact scope and a RAM-only context guard to command clients", async () => {
    expect(mixedElectronicRefundReviewScope(scope)).toEqual(narrowScope);
    let result = await saveMixedElectronicRefund(scope, request.request.selection, preview, alive);
    result = await commandMixedElectronicRefund(scope, result, { kind: "bind" }, alive);
    result = await commandMixedElectronicRefund(
      scope,
      result,
      { kind: "activate", shiftId: id(9) },
      alive,
    );
    result = await commandMixedElectronicRefund(
      scope,
      result,
      { kind: "confirm", confirmation },
      alive,
    );
    mocks.confirmations.mockResolvedValue(completeEvidence);
    result = await commandMixedElectronicRefund(scope, result, { kind: "post", details }, alive);
    expect(result.posted).toEqual(posted);
    for (const client of [mocks.save, mocks.review, mocks.bind, mocks.binding]) {
      expect(client).toHaveBeenCalled();
      for (const call of client.mock.calls)
        expect(
          Object.keys(call[0] as object)
            .filter((key) => key !== "contextCheck")
            .sort(),
        ).toEqual(Object.keys(narrowScope).sort());
    }
    for (const client of [
      mocks.activate,
      mocks.activation,
      mocks.confirm,
      mocks.confirmations,
      mocks.post,
    ]) {
      expect(client).toHaveBeenCalled();
      for (const call of client.mock.calls) {
        const actual = call[0] as typeof scope & { contextCheck?: () => void };
        expect(actual).toEqual({
          ...scope,
          ...(actual.contextCheck === undefined ? {} : { contextCheck: expect.any(Function) }),
        });
      }
    }
    for (const client of [mocks.save, mocks.bind, mocks.activate, mocks.confirm, mocks.post])
      for (const call of client.mock.calls)
        expect(call[0]).toEqual({ ...scope, contextCheck: expect.any(Function) });
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
          ? saveMixedElectronicRefund(scope, request.request.selection, preview, alive)
          : commandMixedElectronicRefund(
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
      const pending = loadMixedElectronicRefund(scope);
      expect(pending?.request).toEqual(request);
      expect(pending?.attempted).toBe(phase);
      const counts = writes().map((write) => write.mock.calls.length);
      expect((await inspectMixedElectronicRefund(scope))?.pending).toEqual(pending);
      expect(writes().map((write) => write.mock.calls.length)).toEqual(counts);
      expect(mocks.review).toHaveBeenLastCalledWith(narrowScope, request);
      await expect(
        saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
      ).rejects.toThrow("Журнал изменился");
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
      await expect(inspectMixedElectronicRefund(scope)).rejects.toThrow("Unavailable");
      expect(loadMixedElectronicRefund(scope)).toEqual(pending);
      await expect(
        saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
      ).rejects.toThrow();
      expect(mocks.uuid).not.toHaveBeenCalled();
      expectNoCommands();
    },
  );
  it("does not inspect or submit a damaged journal and never deletes it", async () => {
    localStorage.setItem(mixedElectronicRefundKey(scope), "{broken");
    await expect(inspectMixedElectronicRefund(scope)).rejects.toThrow("повреждён");
    await expect(
      saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
    ).rejects.toThrow("повреждён");
    expect(localStorage.getItem(mixedElectronicRefundKey(scope))).toBe("{broken");
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
      localStorage.setItem(mixedElectronicRefundKey(scope), raw);
      await expect(inspectMixedElectronicRefund(scope)).rejects.toThrow("повреждён");
      expect(localStorage.getItem(mixedElectronicRefundKey(scope))).toBe(raw);
      expectNoCommands();
    },
  );
  it("blocks commands when storage cannot persist the initial journal", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Quota");
    });
    await expect(
      saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
    ).rejects.toThrow("не отправлена");
    expectNoCommands();
  });
  it("blocks when a storage write cannot be read back", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
    await expect(
      saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
    ).rejects.toThrow("не отправлена");
    expectNoCommands();
  });
  it("blocks inspection and commands when storage reads are denied", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("Denied");
    });
    await expect(inspectMixedElectronicRefund(scope)).rejects.toThrow("недоступно");
    await expect(
      saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
    ).rejects.toThrow("недоступно");
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
      expect((await inspectMixedElectronicRefund(scope))?.posted).toEqual(posted);
      await expect(
        commandMixedElectronicRefund(scope, current("save"), { kind: "bind" }, alive),
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
    await expect(
      commandMixedElectronicRefund(scope, result, { kind: "bind" }, alive),
    ).rejects.toThrow(/Контекст/);
    expect(loadMixedElectronicRefund(scope)?.attempted).toBe("save");
    expectNoCommands();
  });
  it("requires Web Locks before inspecting or issuing commands", async () => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await expect(inspectMixedElectronicRefund(scope)).rejects.toThrow("Web Locks");
    await expect(
      saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
    ).rejects.toThrow("Web Locks");
    expectNoCommands();
  });
  it("serializes simultaneous tabs into one UUID and one save command", async () => {
    const results = await Promise.allSettled([
      saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
      saveMixedElectronicRefund(scope, request.request.selection, preview, alive),
    ]);
    expect(results.filter((result) => result.status === "fulfilled")).toHaveLength(1);
    expect(mocks.uuid).toHaveBeenCalledTimes(1);
    expect(mocks.save).toHaveBeenCalledTimes(1);
  });
  it("isolates all five scope fields", () => {
    stored();
    for (const field of Object.keys(scope) as Array<keyof typeof scope>) {
      const changed = { ...scope, [field]: id(99) };
      expect(mixedElectronicRefundKey(changed)).not.toBe(mixedElectronicRefundKey(scope));
      expect(loadMixedElectronicRefund(changed)).toBeNull();
    }
  });
  it("rejects a late response after session generation changes and keeps the operation", async () => {
    stored("post");
    mocks.review.mockImplementationOnce(() => {
      mocks.generation += 1;
      return Promise.resolve(review);
    });
    await expect(inspectMixedElectronicRefund(scope)).rejects.toThrow(/Контекст/);
    expect(mocks.binding).not.toHaveBeenCalled();
    expect(loadMixedElectronicRefund(scope)).not.toBeNull();
    expectNoCommands();
  });
  it("does not use a result after the modal unmounts", async () => {
    stored();
    let mounted = true;
    mocks.review.mockImplementationOnce(() => {
      mounted = false;
      return Promise.resolve(review);
    });
    await expect(inspectMixedElectronicRefund(scope, () => mounted)).rejects.toThrow(
      "Контекст изменился",
    );
    expect(loadMixedElectronicRefund(scope)).not.toBeNull();
    expectNoCommands();
  });
  it("rejects storage changes made while a GET is in flight", async () => {
    stored("bind");
    mocks.review.mockImplementationOnce(() => {
      stored("cancel");
      return Promise.resolve(review);
    });
    await expect(inspectMixedElectronicRefund(scope)).rejects.toThrow("Журнал изменился");
    expect(mocks.binding).not.toHaveBeenCalled();
    expect(loadMixedElectronicRefund(scope)?.attempted).toBe("cancel");
    expectNoCommands();
  });
  it("rechecks permission after awaited reads before a command", async () => {
    const result = current("save");
    mocks.review.mockImplementationOnce(() => {
      mocks.refund = false;
      return Promise.resolve(review);
    });
    await expect(
      commandMixedElectronicRefund(scope, result, { kind: "bind" }, alive),
    ).rejects.toThrow(/доступ/i);
    expectNoCommands();
  });
  it("keeps the previous journal when context changes as authority resolves", async () => {
    const result = current("save");
    const previous = localStorage.getItem(mixedElectronicRefundKey(scope));
    const shift = {
      id: id(9),
      status: "open",
      opened_by_user_id: scope.userId,
      branch_id: scope.branchId,
      register_id: scope.registerId,
      currency: "TJS",
    };
    mocks.shift.mockImplementationOnce(() => ({
      then(resolve: (value: typeof shift) => void) {
        resolve(shift);
        queueMicrotask(() => {
          mocks.generation += 1;
        });
      },
    }));
    await expect(
      commandMixedElectronicRefund(scope, result, { kind: "bind" }, alive),
    ).rejects.toThrow(/Контекст/);
    expect(localStorage.getItem(mixedElectronicRefundKey(scope))).toBe(previous);
    expectNoCommands();
  });
  it("permits readonly GET recovery while blocking new commands", async () => {
    stored("post");
    mocks.confirmations.mockResolvedValue(completeEvidence);
    mocks.readonly = true;
    expect((await inspectMixedElectronicRefund(scope))?.posted).toEqual(posted);
    await expect(
      commandMixedElectronicRefund(scope, current("save"), { kind: "bind" }, alive),
    ).rejects.toThrow("только для чтения");
    expectNoCommands();
  });
  it("requires external confirmation permission independently", async () => {
    const result = current("activate");
    mocks.external = false;
    await expect(
      commandMixedElectronicRefund(scope, result, { kind: "confirm", confirmation }, alive),
    ).rejects.toThrow("Недостаточно прав");
    expect((await inspectMixedElectronicRefund(scope))?.evidence).toEqual(evidence);
    expectNoCommands();
  });
  it("blocks post while confirmations are incomplete", async () => {
    await expect(
      commandMixedElectronicRefund(scope, current("activate"), { kind: "post", details }, alive),
    ).rejects.toThrow("предыдущий этап");
    expectNoCommands();
  });
  it("does not reconfirm an already confirmed payment", async () => {
    mocks.confirmations.mockResolvedValue(completeEvidence);
    await expect(
      commandMixedElectronicRefund(
        scope,
        current("confirm"),
        { kind: "confirm", confirmation },
        alive,
      ),
    ).rejects.toThrow("уже подтверждена");
    expectNoCommands();
  });
  it("cannot cancel after activation", async () => {
    await expect(
      commandMixedElectronicRefund(scope, current("activate"), { kind: "cancel" }, alive),
    ).rejects.toThrow("предыдущий этап");
    expectNoCommands();
  });
  it("does not send a command if persisting its phase fails", async () => {
    const result = current("save");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Quota");
    });
    await expect(
      commandMixedElectronicRefund(scope, result, { kind: "bind" }, alive),
    ).rejects.toThrow("не отправлена");
    expect(loadMixedElectronicRefund(scope)?.attempted).toBe("save");
    expectNoCommands();
  });
  it("acknowledges posted only after fresh GETs and removes only its journal", async () => {
    stored("post");
    mocks.confirmations.mockResolvedValue(completeEvidence);
    expect(await acknowledgeMixedElectronicRefund(scope, id(6), alive)).toBe(posted.sale_id);
    for (const read of reads()) expect(read).toHaveBeenCalledTimes(1);
    expect(loadMixedElectronicRefund(scope)).toBeNull();
    expectNoCommands();
  });
  it("cannot acknowledge an unknown or mismatched operation", async () => {
    stored("post");
    mocks.posting.mockRejectedValueOnce(new Error("Response lost"));
    await expect(acknowledgeMixedElectronicRefund(scope, id(99), alive)).rejects.toThrow(
      "изменилась",
    );
    await expect(acknowledgeMixedElectronicRefund(scope, id(6), alive)).rejects.toThrow(
      "Response lost",
    );
    expect(loadMixedElectronicRefund(scope)).not.toBeNull();
    expectNoCommands();
  });
  it("retains pending when acknowledgment removal is denied", async () => {
    stored("post");
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("Denied");
    });
    await expect(acknowledgeMixedElectronicRefund(scope, id(6), alive)).rejects.toThrow("оставлен");
    expect(loadMixedElectronicRefund(scope)).not.toBeNull();
    expectNoCommands();
  });
  it("allows acknowledging verified cancellation but never an unposted review", async () => {
    stored("save");
    await expect(acknowledgeMixedElectronicRefund(scope, id(6), alive)).rejects.toThrow(
      "не подтверждено",
    );
    stored("cancel");
    mocks.binding.mockResolvedValue({
      ...binding,
      state: "cancelled",
      cancelled_at: "2026-10-20T06:00:04Z",
    });
    expect(await acknowledgeMixedElectronicRefund(scope, id(6), alive)).toBe(scope.parentSaleId);
    expect(loadMixedElectronicRefund(scope)).toBeNull();
    expectNoCommands();
  });
});
