import {
  storeUnitPaymentMarker,
  loadUnitPaymentMarker,
  clearUnitPaymentMarker,
} from "@/features/pos/unitPaymentPreparationStorage";
import { installElectronicLocks } from "./unitElectronicCheckoutFixture";
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import {
  executeElectronicCommand,
  cancelElectronicPreparation,
  hasRejectedElectronicCommand,
  releaseRejectedElectronicCommand,
  loadElectronicCommand,
  parseElectronicAttempts,
  parseElectronicResult,
  recoverElectronicCheckout,
  voidSchema,
} from "@/features/pos/unitElectronicCheckout";
import { authenticatePos } from "./posIdentityFixture";
import { id, scope, cancelled, bound } from "./unitPaymentPreparationFixture";

import { context, attempt, result } from "./unitElectronicCheckoutFixture";
const notFound = () => ({ isAxiosError: true, response: { status: 404 } });
beforeEach(() => {
  installElectronicLocks();
  localStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
  authenticatePos(scope);
  const c = context();
  storeUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope, c.intent.operation_id);
  vi.spyOn(api, "get").mockRejectedValue(notFound());
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("physical electronic checkout exact contracts", () => {
  it("rejects partial, duplicate or alien attempts and physical scope before writes", async () => {
    const c = context();
    for (const data of [
      [],
      [attempt(), attempt()],
      [{ ...attempt(), tenant_id: id(99) }],
      [{ ...attempt(), amount: "2.00" }],
      [{ ...attempt(), operation_id: id(22) }],
    ])
      expect(() => parseElectronicAttempts(data, c)).toThrow();
    const post = vi.spyOn(api, "post");
    await expect(
      executeElectronicCommand({ ...c, registerId: id(99) }, { action: "complete", payload: {} }),
    ).rejects.toThrow();
    await expect(
      executeElectronicCommand(c, { action: "attempts", payload: { attempt_ids: { qr: id(21) } } }),
    ).rejects.toThrow();
    expect(post).not.toHaveBeenCalled();
  });
  it("recovery on missing checkout and attempts is GET only", async () => {
    const get = vi.spyOn(api, "get").mockRejectedValue(notFound());
    const post = vi.spyOn(api, "post");
    expect(await recoverElectronicCheckout(context())).toEqual({ attempts: [], result: null });
    expect(get.mock.calls.map((c) => c[0])).toEqual([
      expect.stringContaining("/checkout"),
      expect.stringContaining("/attempts"),
    ]);
    expect(post).not.toHaveBeenCalled();
  });
  it("persists exact full UUID map before sending, retains ambiguity and replays same payload", async () => {
    const c = context(),
      command = { action: "attempts" as const, payload: { attempt_ids: { card: id(21) } } };
    const post = vi
      .spyOn(api, "post")
      .mockImplementationOnce(async () => {
        expect(loadElectronicCommand(c)).toEqual(command);
        throw new Error("lost response");
      })
      .mockResolvedValueOnce({ data: [attempt()] });
    await expect(executeElectronicCommand(c, command)).rejects.toThrow("lost response");
    const get = vi.spyOn(api, "get").mockRejectedValue(notFound());
    expect((await recoverElectronicCheckout(c)).attempts).toHaveLength(0);
    expect(post).toHaveBeenCalledTimes(1);
    await expect(
      executeElectronicCommand(c, { action: "complete", payload: {} }),
    ).rejects.toThrow();
    await executeElectronicCommand(c, loadElectronicCommand(c)!);
    expect(post.mock.calls[0]?.[1]).toEqual(post.mock.calls[1]?.[1]);
    expect(loadElectronicCommand(c)).toBeNull();
    expect(get).toHaveBeenCalledTimes(3);
  });
  it("does not POST after storage fails or after an awaited session/register fence changes", async () => {
    const c = context(),
      post = vi.spyOn(api, "post");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    await expect(
      executeElectronicCommand(c, { action: "complete", payload: {} }),
    ).rejects.toThrow();
    expect(post).not.toHaveBeenCalled();
    vi.restoreAllMocks();
    let active = true;
    const get = vi.spyOn(api, "get").mockImplementationOnce(async () => {
      active = false;
      return { data: result() };
    });
    await expect(recoverElectronicCheckout({ ...c, isActive: () => active })).rejects.toThrow();
    expect(get).toHaveBeenCalledTimes(1);
  });
  it("recovers confirmed and completed facts without commands or current catalogue requests", async () => {
    const c = context(),
      post = vi.spyOn(api, "post");
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce({ data: result() })
      .mockResolvedValueOnce({ data: [attempt("consumed")] });
    const recovered = await recoverElectronicCheckout(c);
    expect(recovered.result?.lines[0]?.name).toBe("Historical name");
    expect(post).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledTimes(2);
    for (const data of [
      { ...result(), register_id: id(99) },
      { ...result(), operation_id: id(99) },
      { ...result(), is_test: true },
      { ...result(), payments: [{ ...result().payments[0], amount: "2.00" }] },
      { ...result(), lines: result().lines.map((l) => ({ ...l, quantity: 3 })) },
    ])
      expect(() => parseElectronicResult(data, c, [attempt("consumed")])).toThrow();
    expect(() => parseElectronicResult(result(), c, [attempt("confirmed")])).toThrow();
  });
  it("resolves a lost confirm payload only from exact GET facts", async () => {
    const c = context(),
      command = {
        action: "confirm" as const,
        attemptId: id(21),
        payload: { terminal_id: "T1", external_reference: "R1" },
      };
    const post = vi.spyOn(api, "post").mockRejectedValue(new Error("lost confirm"));
    await expect(executeElectronicCommand(c, command)).rejects.toThrow();
    vi.spyOn(api, "get")
      .mockRejectedValueOnce(notFound())
      .mockResolvedValueOnce({ data: [attempt("confirmed")] });
    const state = await recoverElectronicCheckout(c);
    expect(state.attempts[0]?.status).toBe("confirmed");
    expect(loadElectronicCommand(c)).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("GET-fact command resolution", () => {
  it.each(["confirmed", "consumed"] as const)(
    "resolves lost reconciliation after advancement to %s without POST",
    async (status) => {
      const c = context(),
        post = vi.spyOn(api, "post").mockRejectedValue(new Error("lost"));
      await expect(
        executeElectronicCommand(c, { action: "reconciliation", attemptId: id(21), payload: {} }),
      ).rejects.toThrow();
      vi.spyOn(api, "get")
        .mockRejectedValueOnce(notFound())
        .mockResolvedValueOnce({ data: [attempt(status)] });
      await recoverElectronicCheckout(c);
      expect(loadElectronicCommand(c)).toBeNull();
      expect(post).toHaveBeenCalledTimes(1);
    },
  );
  it("keeps ambiguous/mismatched evidence and exact original payload", async () => {
    const c = context(),
      command = {
        action: "confirm" as const,
        attemptId: id(21),
        payload: { terminal_id: "T1", external_reference: "R1" },
      };
    const post = vi.spyOn(api, "post").mockRejectedValue(new Error("lost"));
    await expect(executeElectronicCommand(c, command)).rejects.toThrow();
    vi.spyOn(api, "get")
      .mockRejectedValueOnce(notFound())
      .mockResolvedValueOnce({
        data: [{ ...attempt("confirmed"), external_reference: "another" }],
      });
    await recoverElectronicCheckout(c);
    expect(loadElectronicCommand(c)).toEqual(command);
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("resolves lost complete using only saved result and consumed attempts", async () => {
    const c = context(),
      post = vi.spyOn(api, "post").mockRejectedValue(new Error("lost"));
    await expect(
      executeElectronicCommand(c, { action: "complete", payload: {} }),
    ).rejects.toThrow();
    vi.spyOn(api, "get")
      .mockResolvedValueOnce({ data: result() })
      .mockResolvedValueOnce({ data: [attempt("consumed")] });
    expect((await recoverElectronicCheckout(c)).result?.receipt_number).toBe("001");
    expect(loadElectronicCommand(c)).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("combined cash and QR saved allocation", () => {
  it("validates exact full card+QR maps and cash+QR historical payments without new quote", async () => {
    const { hash } = await import("./unitPaymentPreparationFixture");
    const c = context();
    c.intent = structuredClone(c.intent);
    c.intent.plan.request.payments = [
      { method: "cash", amount: "0.99" },
      { method: "qr", amount: "1.00" },
    ];
    c.intent.plan.request.cash_tendered = "1.00";
    c.intent.plan.change_amount = "0.01";
    c.intent.plan.plan_hash = hash({
      kind: "unit_payment_plan_v1",
      tenant_id: scope.tenantId,
      branch_id: c.branchId,
      cashier_id: scope.userId,
      sale_id: c.intent.plan.request.sale_id,
      revision_hash: c.intent.plan.request.expected_revision_hash,
      currency: "TJS",
      payments: c.intent.plan.request.payments,
      cash_tendered: "1.00",
    });
    c.binding = { ...c.binding, plan_hash: c.intent.plan.plan_hash };
    const qr = { ...attempt("consumed"), payment_method: "qr" as const, amount: "1.00" };
    const doc = {
      ...result(),
      payments: [
        { id: c.intent.operation_id, method: "cash", amount: "0.99", currency: "TJS" },
        { id: qr.id, method: "qr", amount: "1.00", currency: "TJS" },
      ],
    };
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce({ data: doc })
      .mockResolvedValueOnce({ data: [qr] });
    expect((await recoverElectronicCheckout(c)).result?.payments).toHaveLength(2);
    expect(get.mock.calls.map((call) => call[0])).toEqual([
      expect.stringContaining("/checkout"),
      expect.stringContaining("/attempts"),
    ]);
    const dual = context();
    dual.intent = structuredClone(dual.intent);
    dual.intent.plan.request.payments = [
      { method: "card", amount: "0.99" },
      { method: "qr", amount: "1.00" },
    ];
    const rows = [
      { ...attempt(), amount: "0.99" },
      { ...attempt(), id: id(22), operation_id: id(22), payment_method: "qr", amount: "1.00" },
    ];
    expect(parseElectronicAttempts(rows, dual)).toHaveLength(2);
    expect(() => parseElectronicAttempts(rows.slice(0, 1), dual)).toThrow();
    expect(() =>
      parseElectronicAttempts([{ ...rows[0], evidence_required: false }, rows[1]], dual),
    ).toThrow();
  });
});

describe("evidence and foreign provenance rejection", () => {
  it("rejects incomplete reconciliation provenance, altered scope, and unsafe void notes", () => {
    const c = context();
    for (const row of [
      { ...attempt(), evidence_required: false },
      { ...attempt(), cashier_user_id: id(90) },
      { ...attempt(), sale_id: id(90) },
      { ...attempt("confirmed"), reconciliation_started_at: null },
      { ...attempt("consumed"), consumed_at: null },
      { ...attempt("confirmed"), terminal_id: "\n" },
      { ...attempt("confirmed"), external_reference: "" },
    ])
      expect(() => parseElectronicAttempts([row], c)).toThrow();
    for (const note of [
      "name@example.test",
      "https://example.test",
      "www.example.test",
      "123456",
      "١٢٣٤٥٦",
    ]) {
      expect(
        voidSchema.safeParse({ reason: "cashier_cancelled", operator_note: note }).success,
      ).toBe(false);
    }
    for (const doc of [
      { ...result(), branch_id: id(91) },
      { ...result(), cashier_id: id(91) },
      { ...result(), sale_id: id(91) },
      { ...result(), tenant_id: id(91) },
      { ...result(), stock_command_id: id(91) },
      { ...result(), event_id: id(91) },
    ])
      expect(() => parseElectronicResult(doc, c, [attempt("consumed")])).toThrow();
  });
});

describe("lost void exact resolution", () => {
  it("retains a mismatched void then resolves only matching reason, evidence and note via GET", async () => {
    const c = context(),
      command = {
        action: "void" as const,
        attemptId: id(21),
        payload: {
          reason: "terminal_declined" as const,
          terminal_id: "T1",
          external_reference: "R1",
          operator_note: "Declined",
        },
      };
    const post = vi.spyOn(api, "post").mockRejectedValue(new Error("lost void"));
    await expect(executeElectronicCommand(c, command)).rejects.toThrow();
    const row = {
      ...attempt("voided"),
      terminal_id: "T1",
      external_reference: "R1",
      void_reason: "terminal_declined",
      void_note: "different",
      voided_at: "2026-10-20T12:04:00Z",
    };
    const get = vi
      .spyOn(api, "get")
      .mockRejectedValueOnce(notFound())
      .mockResolvedValueOnce({ data: [row] });
    await recoverElectronicCheckout(c);
    expect(loadElectronicCommand(c)).toEqual(command);
    get
      .mockRejectedValueOnce(notFound())
      .mockResolvedValueOnce({ data: [{ ...row, void_note: "Declined" }] });
    await recoverElectronicCheckout(c);
    expect(loadElectronicCommand(c)).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
  });
});

it("serializes two tabs before UUID persistence and refuses an obsolete different map", async () => {
  const c = context();
  const first = { action: "attempts" as const, payload: { attempt_ids: { card: id(21) } } };
  const second = { action: "attempts" as const, payload: { attempt_ids: { card: id(22) } } };
  let finish: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    finish = resolve;
  });
  let entered: () => void = () => undefined;
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  let rows: ReturnType<typeof attempt>[] | null = null;
  vi.spyOn(api, "get").mockImplementation(async () => {
    if (rows === null) throw notFound();
    return { data: rows };
  });
  const post = vi.spyOn(api, "post").mockImplementation(async () => {
    entered();
    await gate;
    rows = [attempt()];
    return { data: rows };
  });
  const a = executeElectronicCommand(c, first);
  await started;
  const b = executeElectronicCommand(context(), second);
  const rejected = expect(b).rejects.toThrow();
  await Promise.resolve();
  expect(loadElectronicCommand(c)).toEqual(first);
  expect(post).toHaveBeenCalledTimes(1);
  finish();
  await a;
  await rejected;
  expect(post).toHaveBeenCalledTimes(1);
  expect(loadElectronicCommand(c)).toBeNull();
  expect(rows?.[0]?.id).toBe(id(21));
});

it("fails closed without browser locks and when a waiting tab loses its session", async () => {
  Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
  const post = vi.spyOn(api, "post");
  const get = vi.spyOn(api, "get");
  await expect(recoverElectronicCheckout(context())).rejects.toThrow("межвкладочной");
  expect(post).not.toHaveBeenCalled();
  expect(get).not.toHaveBeenCalled();
  installElectronicLocks();
  let finish: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    finish = resolve;
  });
  let entered: () => void = () => undefined;
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  get.mockImplementationOnce(async () => {
    entered();
    await gate;
    throw notFound();
  });
  const a = recoverElectronicCheckout(context());
  await started;
  let active = true;
  const b = recoverElectronicCheckout({ ...context(), isActive: () => active });
  const rejected = expect(b).rejects.toThrow();
  active = false;
  finish();
  await a;
  await rejected;
  expect(get).toHaveBeenCalledTimes(2);
});

it("allows explicit correction only after recognized rollback and fresh unapplied scoped facts", async () => {
  const c = context();
  storeUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope, c.intent.operation_id);
  const command = {
    action: "confirm" as const,
    attemptId: id(21),
    payload: { terminal_id: "T1", external_reference: "R1" },
  };
  const conflict = {
    isAxiosError: true,
    response: {
      status: 409,
      data: { error: { code: "conflict", message: "Terminal payment reference was already used" } },
    },
  };
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(conflict);
  await expect(executeElectronicCommand(c, command)).rejects.toThrow("отклонил");
  expect(hasRejectedElectronicCommand(c)).toBe(true);
  await expect(executeElectronicCommand(c, command)).rejects.toThrow("отклонил");
  expect(post).toHaveBeenCalledTimes(1);
  vi.spyOn(api, "get")
    .mockRejectedValueOnce(notFound())
    .mockResolvedValueOnce({ data: [attempt("requires_reconciliation")] });
  await releaseRejectedElectronicCommand(c);
  expect(loadElectronicCommand(c)).toBeNull();
  expect(loadUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope)).toBe(c.intent.operation_id);
  const corrected = {
    ...command,
    payload: { ...command.payload, external_reference: "CORRECTED" },
  };
  const confirmed = { ...attempt("confirmed"), external_reference: "CORRECTED" };
  post.mockResolvedValueOnce({ data: confirmed });
  vi.spyOn(api, "get")
    .mockRejectedValueOnce(notFound())
    .mockResolvedValueOnce({ data: [confirmed] });
  await executeElectronicCommand(c, corrected);
  expect(post.mock.calls[1]?.[0]).toContain(`/attempts/${id(21)}/confirm`);
  expect(post.mock.calls[1]?.[1]).toEqual(corrected.payload);
  expect(loadElectronicCommand(c)).toBeNull();
});

it.each([409, 422, 500])(
  "retains unknown HTTP %s rejection payload without offering correction",
  async (status) => {
    const c = context();
    const command = {
      action: "confirm" as const,
      attemptId: id(21),
      payload: { terminal_id: "T1", external_reference: "R1" },
    };
    vi.spyOn(api, "post").mockRejectedValue({
      isAxiosError: true,
      response: { status, data: { error: { code: "conflict", message: "unknown" } } },
    });
    await expect(executeElectronicCommand(c, command)).rejects.toBeDefined();
    expect(loadElectronicCommand(c)).toEqual(command);
    expect(hasRejectedElectronicCommand(c)).toBe(false);
    await expect(releaseRejectedElectronicCommand(c)).rejects.toThrow();
    expect(loadElectronicCommand(c)).toEqual(command);
  },
);

it("does not poison a newer preparation after the attempts GET", async () => {
  const c = context();
  const post = vi.spyOn(api, "post");
  vi.spyOn(api, "get").mockImplementationOnce(async () => {
    clearUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope, c.intent.operation_id);
    storeUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope, id(99));
    throw notFound();
  });
  await expect(
    executeElectronicCommand(c, { action: "attempts", payload: { attempt_ids: { card: id(21) } } }),
  ).rejects.toThrow();
  expect(post).not.toHaveBeenCalled();
  expect(loadElectronicCommand(c)).toBeNull();
});

it("serializes cancellation behind another tab's attempts and retains its financial barrier", async () => {
  const c = context();
  let finish: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    finish = resolve;
  });
  let entered: () => void = () => undefined;
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  let exists = false;
  vi.spyOn(api, "get").mockImplementation(async (url) => {
    if (String(url).endsWith("/binding")) return { data: bound };
    if (String(url).endsWith("/checkout") || !exists) throw notFound();
    return { data: [attempt()] };
  });
  vi.spyOn(api, "post").mockImplementation(async () => {
    entered();
    await gate;
    exists = true;
    return { data: [attempt()] };
  });
  const a = executeElectronicCommand(c, {
    action: "attempts",
    payload: { attempt_ids: { card: id(21) } },
  });
  await started;
  const cancel = vi.fn().mockResolvedValue(cancelled);
  const b = cancelElectronicPreparation(
    {
      intent: c.intent,
      scope: c.scope,
      branchId: c.branchId,
      registerId: c.registerId,
      isActive: c.isActive,
    },
    cancel,
  );
  const rejected = expect(b).rejects.toThrow();
  expect(cancel).not.toHaveBeenCalled();
  finish();
  await a;
  await rejected;
  expect(cancel).not.toHaveBeenCalled();
  expect(loadUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope)).toBe(c.intent.operation_id);
});
